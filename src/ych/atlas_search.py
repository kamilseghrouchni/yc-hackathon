import dataclasses

import anndata as ad
import duckdb
import lancedb
import mudata as mu
import numpy as np
import pandas as pd
import polars as pl
import pyarrow as pa
import scipy.sparse as sp
from lancedb.query import FullTextOperator, MatchQuery

from ych.ingestion_utils import lookup_gene_indices_from_table
from ych.schema import FEATURE_SPACE_TO_TABLE

# Scalar-indexed columns on modality tables that support exact-match filtering.
_SCALAR_FIELDS: dict[str, type] = {
    "dataset_uid": str,
    "assay": str,
    "is_control": bool,
}

# Metadata columns present on all denormalized data tables (from _CellMetadataMixin).
_METADATA_COLUMNS = [
    "cell_uid",
    "dataset_uid",
    "assay",
    "additional_metadata",
    "is_control",
    "chemical_perturbation_uid",
    "chemical_perturbation_concentration",
    "chemical_perturbation_additional_metadata",
    "genetic_perturbation_gene_index",
    "genetic_perturbation_method",
    "genetic_perturbation_concentration",
    "genetic_perturbation_additional_metadata",
    "perturbation_search_string",
]

# Metadata columns used for obs (everything except search string)
_OBS_METADATA_COLUMNS = [c for c in _METADATA_COLUMNS if c != "perturbation_search_string"]


@dataclasses.dataclass
class AtlasQuery:
    """Structured query for filtering cells across multi-table schema."""

    # Scalar-indexed filters (exact match)
    dataset_uid: str | None = None
    assay: str | None = None
    is_control: bool | None = None

    # Used for gene name resolution (not a cell metadata filter)
    organism: str | None = None

    # Perturbation label filters (FTS index on perturbation_search_string)
    gene_names: list[str] | None = None
    ensembl_ids: list[str] | None = None
    perturbation_method: str | None = None
    chemical_perturbation_uid: str | None = None

    # Post-filter on concentration (applied via duckdb after LanceDB query)
    genetic_perturbation_concentration_min: float | None = None
    genetic_perturbation_concentration_max: float | None = None
    chemical_perturbation_concentration_min: float | None = None
    chemical_perturbation_concentration_max: float | None = None

    # Which modality tables to query (defaults to all)
    feature_spaces: list[str] | None = None


def build_search_query(
    db: lancedb.DBConnection,
    query: AtlasQuery,
    operator: FullTextOperator = FullTextOperator.OR,
) -> MatchQuery | None:
    """Build a full-text MatchQuery from perturbation filters."""
    tokens: list[str] = []
    if query.gene_names:
        genes_table = db.open_table("genes")
        gene_index_map = lookup_gene_indices_from_table(
            genes_table, organism=query.organism or "human", gene_names=query.gene_names
        )
        for name in query.gene_names:
            if name in gene_index_map:
                tokens.append(f"GENE_ID:{gene_index_map[name]}")

    if query.ensembl_ids:
        genes_table = db.open_table("genes")
        gene_index_map = lookup_gene_indices_from_table(
            genes_table, organism=query.organism or "human", ensembl_ids=query.ensembl_ids
        )
        for eid in query.ensembl_ids:
            if eid in gene_index_map:
                tokens.append(f"GENE_ID:{gene_index_map[eid]}")

    if query.perturbation_method:
        tokens.append(f"METHOD:{query.perturbation_method}")

    if query.chemical_perturbation_uid:
        tokens.append(f"SM:{query.chemical_perturbation_uid}")

    if not tokens:
        return None

    return MatchQuery(
        " ".join(tokens),
        column="perturbation_search_string",
        operator=operator,
    )


def build_where_clause(query: AtlasQuery) -> str | None:
    """Build a SQL WHERE clause from scalar filters.

    Returns None if no filters are set.
    """
    conditions = []

    for field_name, field_type in _SCALAR_FIELDS.items():
        value = getattr(query, field_name)
        if value is None:
            continue
        if field_type is bool:
            conditions.append(f"{field_name} = {str(value).lower()}")
        else:
            escaped = value.replace("'", "''")
            conditions.append(f"{field_name} = '{escaped}'")

    if not conditions:
        return None
    return " AND ".join(conditions)


def execute_query(
    db: lancedb.DBConnection,
    table_name: str,
    query: AtlasQuery,
    max_records: int | None = None,
    select_cols: list[str] | None = None,
) -> pa.Table:
    """Execute a filtered query directly against a denormalized data table.

    Returns a PyArrow Table with both metadata and data columns.
    """
    search_query = build_search_query(db, query)
    where_clause = build_where_clause(query)

    table = db.open_table(table_name)

    if search_query is not None:
        lance_query = table.search(search_query)
    else:
        lance_query = table.search()

    if where_clause is not None:
        lance_query = lance_query.where(where_clause)

    if select_cols:
        lance_query = lance_query.select(select_cols)

    if max_records is not None:
        lance_query = lance_query.limit(max_records)

    arrow_table = lance_query.to_arrow()
    arrow_table = filter_by_concentration(arrow_table, query)
    return arrow_table


def filter_by_concentration(cells: pa.Table, query: AtlasQuery) -> pa.Table:
    """Post-filter cells by perturbation concentration ranges using duckdb unnest.

    Uses ANY semantics: a cell is kept if at least one perturbation in its list
    falls within the specified concentration range.
    """
    has_genetic = (
        query.genetic_perturbation_concentration_min is not None
        or query.genetic_perturbation_concentration_max is not None
    )
    has_chemical = (
        query.chemical_perturbation_concentration_min is not None
        or query.chemical_perturbation_concentration_max is not None
    )
    if not has_genetic and not has_chemical:
        return cells

    con = duckdb.connect()
    con.register("cells", cells)
    keep_conditions = []

    if has_genetic:
        genetic_conditions = []
        if query.genetic_perturbation_concentration_min is not None:
            genetic_conditions.append(f"conc >= {query.genetic_perturbation_concentration_min}")
        if query.genetic_perturbation_concentration_max is not None:
            genetic_conditions.append(f"conc <= {query.genetic_perturbation_concentration_max}")
        genetic_where = " AND ".join(genetic_conditions)
        keep_conditions.append(
            f"""cell_uid IN (
                SELECT cell_uid FROM (
                    SELECT cell_uid, unnest(genetic_perturbation_concentration) AS conc
                    FROM cells
                    WHERE genetic_perturbation_concentration IS NOT NULL
                ) WHERE {genetic_where}
            )"""
        )

    if has_chemical:
        chemical_conditions = []
        if query.chemical_perturbation_concentration_min is not None:
            chemical_conditions.append(f"conc >= {query.chemical_perturbation_concentration_min}")
        if query.chemical_perturbation_concentration_max is not None:
            chemical_conditions.append(f"conc <= {query.chemical_perturbation_concentration_max}")
        chemical_where = " AND ".join(chemical_conditions)
        keep_conditions.append(
            f"""cell_uid IN (
                SELECT cell_uid FROM (
                    SELECT cell_uid, unnest(chemical_perturbation_concentration) AS conc
                    FROM cells
                    WHERE chemical_perturbation_concentration IS NOT NULL
                ) WHERE {chemical_where}
            )"""
        )

    full_where = " AND ".join(keep_conditions)
    result = con.execute(f"SELECT * FROM cells WHERE {full_where}").fetch_arrow_table()
    con.close()
    return result


def _unpack_arrow_binary(arrow_col, dtype: np.dtype) -> tuple[np.ndarray, np.ndarray]:
    """Return (offsets_in_elements, flat_data) from a binary Arrow column.

    Handles both regular and large binary Arrow types by detecting whether
    offsets are 32-bit or 64-bit. The returned offsets are converted from byte
    offsets to element offsets using ``dtype.itemsize``.
    """
    offsets_dtype = np.int64 if "Large" in type(arrow_col).__name__ else np.int32
    byte_offsets = np.frombuffer(arrow_col.buffers()[1], dtype=offsets_dtype)
    flat_data = np.frombuffer(arrow_col.buffers()[2], dtype=dtype)
    element_offsets = byte_offsets // dtype.itemsize
    return element_offsets, flat_data


def _reconstruct_gene_expression(
    group_df: pl.DataFrame,
    measured_indices: np.ndarray,
    gene_ensembl_arr: np.ndarray,
    max_gene_index: int,
) -> tuple[sp.csr_matrix, pd.DataFrame, dict]:
    """Reconstruct a sparse CSR matrix for gene expression data."""
    n_features = len(measured_indices)
    n_cells = group_df.height

    global_to_local = np.empty(max_gene_index + 1, dtype=np.int32)
    global_to_local[measured_indices] = np.arange(n_features, dtype=np.int32)

    indices_arrow = group_df["gene_indices"].to_arrow()
    values_arrow = group_df["counts"].to_arrow()

    idx_offsets, all_global_indices = _unpack_arrow_binary(indices_arrow, np.dtype(np.int32))
    val_offsets, all_values = _unpack_arrow_binary(values_arrow, np.dtype(np.float32))

    indptr = idx_offsets[: n_cells + 1].astype(np.int64)
    all_global_indices = all_global_indices[: indptr[-1]]
    all_values = all_values[: indptr[-1]]
    all_local_indices = global_to_local[all_global_indices]

    X = sp.csr_matrix(
        (all_values, all_local_indices, indptr),
        shape=(n_cells, n_features),
    )

    measured_gene_ensembl = gene_ensembl_arr[measured_indices]
    var = pd.DataFrame(
        {"gene_id": measured_gene_ensembl},
        index=measured_gene_ensembl,
    )
    return X, var, {}


def _reconstruct_image_feature_vectors(
    group_df: pl.DataFrame,
    measured_indices: np.ndarray,
    image_feature_names: np.ndarray,
) -> tuple[np.ndarray, pd.DataFrame, dict]:
    """Reconstruct a dense float32 matrix for image features."""
    n_features = len(measured_indices)
    n_cells = group_df.height
    feature_names = image_feature_names[measured_indices]

    values_arrow = group_df["feature_values"].to_arrow()
    offsets, all_values = _unpack_arrow_binary(values_arrow, np.dtype(np.float32))

    # Dense features: all cells have the same number of features, so reshape
    start = offsets[0]
    end = offsets[n_cells]
    X = all_values[start:end].reshape(n_cells, n_features)

    var = pd.DataFrame(index=feature_names)
    return X, var, {}


_FEATURE_SPACE_RECONSTRUCTORS = {
    "gene_expression": _reconstruct_gene_expression,
    "image_features": _reconstruct_image_feature_vectors,
}


@dataclasses.dataclass
class _FeatureLookups:
    """Lazily-loaded reference arrays used during AnnData reconstruction."""

    db: lancedb.DBConnection
    gene_names_arr: np.ndarray
    gene_ensembl_arr: np.ndarray
    max_gene_index: int

    _image_feature_names: np.ndarray | None = dataclasses.field(default=None, repr=False)

    @classmethod
    def from_db(cls, db: lancedb.DBConnection) -> "_FeatureLookups":
        genes_df = db.open_table("genes").search().to_polars()
        max_gene_index = genes_df["gene_index"].max()
        gene_names_arr = np.empty(max_gene_index + 1, dtype=object)
        gene_ensembl_arr = np.empty(max_gene_index + 1, dtype=object)
        indices = genes_df["gene_index"].to_numpy()
        gene_names_arr[indices] = genes_df["gene_name"].to_list()
        gene_ensembl_arr[indices] = genes_df["ensembl_id"].to_list()
        # Fill gene_ensembl_arr with gene names where it is None
        missing_mask = gene_ensembl_arr == None  # noqa: E711
        gene_ensembl_arr[missing_mask] = gene_names_arr[missing_mask]

        return cls(
            db=db,
            gene_names_arr=gene_names_arr,
            gene_ensembl_arr=gene_ensembl_arr,
            max_gene_index=max_gene_index,
        )

    @property
    def image_feature_names(self) -> np.ndarray:
        if self._image_feature_names is None:
            img_df = (
                self.db.open_table("image_features")
                .search()
                .select(["feature_index", "feature_name"])
                .to_polars()
            )
            max_idx = img_df["feature_index"].max()
            arr = np.empty(max_idx + 1, dtype=object)
            arr[img_df["feature_index"].to_numpy()] = img_df["feature_name"].to_list()
            self._image_feature_names = arr
        return self._image_feature_names

    def reconstructor_kwargs(self, feature_space: str, measured_indices: np.ndarray) -> dict:
        """Return the extra keyword arguments needed by the given reconstructor."""
        if feature_space == "gene_expression":
            return {
                "gene_ensembl_arr": self.gene_ensembl_arr,
                "max_gene_index": self.max_gene_index,
            }
        if feature_space == "image_features":
            return {"image_feature_names": self.image_feature_names}
        raise ValueError(f"Unknown feature_space '{feature_space}'")


# ---------------------------------------------------------------------------
# Obs construction
# ---------------------------------------------------------------------------


def _build_obs(
    group_df: pl.DataFrame,
    gene_names_arr: np.ndarray,
) -> pd.DataFrame:
    """Build the obs DataFrame from metadata columns.

    Resolves ``genetic_perturbation_gene_index`` lists to comma-separated
    gene name strings using a vectorized explode/lookup/agg approach.
    """
    available_cols = [c for c in _OBS_METADATA_COLUMNS if c in group_df.columns]
    obs_pl = group_df.select(available_cols)

    if "genetic_perturbation_gene_index" in obs_pl.columns:
        col = obs_pl["genetic_perturbation_gene_index"]
        # Vectorized: explode, lookup via numpy, re-aggregate
        temp = pl.DataFrame(
            {
                "row_nr": pl.arange(0, obs_pl.height, eager=True),
                "idx_list": col,
            }
        )
        exploded = temp.explode("idx_list").filter(pl.col("idx_list").is_not_null())
        if exploded.height > 0:
            looked_up = gene_names_arr[exploded["idx_list"].to_numpy().astype(np.int64)]
            exploded = exploded.with_columns(pl.Series("gene_name", looked_up))
            reagg = exploded.group_by("row_nr", maintain_order=True).agg(
                pl.col("gene_name").str.concat(",").alias("gene_names_str")
            )
            mapping = dict(
                zip(reagg["row_nr"].to_list(), reagg["gene_names_str"].to_list(), strict=False)
            )
            resolved = pl.Series(
                "genetic_perturbation_gene_index",
                [mapping.get(i) for i in range(obs_pl.height)],
                dtype=pl.String,
            )
        else:
            resolved = pl.Series(
                "genetic_perturbation_gene_index",
                [None] * obs_pl.height,
                dtype=pl.String,
            )
        obs_pl = obs_pl.with_columns(resolved)

    return obs_pl.to_pandas().set_index("cell_uid")


def create_anndatas_from_query(
    db: lancedb.DBConnection,
    query: AtlasQuery,
    max_records: int | None = None,
) -> dict[str, dict[str, ad.AnnData]]:
    """Query denormalized data tables directly and reconstruct AnnData/MuData.

    Returns a nested dictionary keyed by ``assay`` → ``dataset_uid``.
    Datasets with a single feature space produce an :class:`~anndata.AnnData`;
    datasets with multiple feature spaces (e.g. paired RNA + protein) produce
    a :class:`~mudata.MuData` whose modalities are keyed by feature space.
    """
    lookups = _FeatureLookups.from_db(db)

    feature_spaces_to_query = list(FEATURE_SPACE_TO_TABLE.keys())
    if query.feature_spaces:
        feature_spaces_to_query = [
            fs for fs in feature_spaces_to_query if fs in query.feature_spaces
        ]

    # Collect all dataset_uids across tables so we can batch-load dataset metadata.
    all_dataset_uids: set[str] = set()
    table_results: dict[str, pl.DataFrame] = {}

    for feature_space in feature_spaces_to_query:
        table_name = FEATURE_SPACE_TO_TABLE[feature_space]
        arrow_table = execute_query(db, table_name, query, max_records)
        if arrow_table.num_rows == 0:
            continue
        table_results[feature_space] = pl.from_arrow(arrow_table)
        all_dataset_uids.update(arrow_table.column("dataset_uid").to_pylist())

    if not table_results:
        return {}

    # Load dataset metadata for measured_feature_indices lookup
    uid_list = ", ".join(f"'{uid}'" for uid in all_dataset_uids)
    datasets_df = (
        db.open_table("datasets")
        .search()
        .where(f"dataset_uid IN ({uid_list})")
        .select(["dataset_uid", "measured_feature_indices", "feature_space"])
        .to_polars()
    )

    dataset_measured: dict[tuple[str, str], bytes] = {}
    for row in datasets_df.iter_rows(named=True):
        key = (row["dataset_uid"], row["feature_space"])
        dataset_measured[key] = row["measured_feature_indices"]

    per_dataset: dict[str, dict[str, dict[str, ad.AnnData]]] = {}

    for feature_space, result_pl in table_results.items():
        for (dataset_uid,), group_df in result_pl.group_by(["dataset_uid"]):
            key = (dataset_uid, feature_space)
            if key not in dataset_measured:
                continue
            measured_indices = np.frombuffer(dataset_measured[key], dtype=np.int32)
            assay = group_df["assay"][0]

            reconstruct = _FEATURE_SPACE_RECONSTRUCTORS.get(feature_space)
            if reconstruct is None:
                raise ValueError(
                    f"Unknown feature_space '{feature_space}' for dataset {dataset_uid}"
                )

            extra_kwargs = lookups.reconstructor_kwargs(feature_space, measured_indices)
            X, var, obsm = reconstruct(group_df, measured_indices, **extra_kwargs)
            obs = _build_obs(group_df, lookups.gene_names_arr)

            adata = ad.AnnData(obs=obs, var=var, obsm=obsm)
            if X is not None:
                adata.X = X
            per_dataset.setdefault(assay, {}).setdefault(dataset_uid, {})[feature_space] = adata

    return per_dataset
