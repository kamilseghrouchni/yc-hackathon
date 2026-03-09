"""Library utilities imported by GEO ingestion scripts.

Generated ingestion scripts import from this module for gene/molecule resolution,
sparse count extraction, and metadata construction.
"""

import json
from enum import Enum
from typing import Literal

import lancedb
import numpy as np
import pandas as pd
import scipy.sparse as sp
from Bio import Entrez

from ych.schema import (
    GeneExpressionRecord,
    GeneSchema,
    ImageFeatureVectorRecord,
    ImageFeatureSchema,
    MoleculeSchema,
    PublicationSchema,
)

Entrez.email = "ryan@epiblast.ai"
CellDataRecord = (
    GeneExpressionRecord
    | ImageFeatureVectorRecord
)


def _escape_lance_value(value: str) -> str:
    """Escape single quotes in a string value for LanceDB WHERE clauses."""
    return value.replace("'", "''")


class OntologyEntity(Enum):
    GENE: str = "Gene"
    PROTEIN: str = "Protein"
    ORGANISM: str = "Organism"
    CELL_LINE: str = "CellLine"
    CELL_TYPE: str = "CellType"
    TISSUE: str = "Tissue"
    DISEASE: str = "Disease"
    DEVELOPMENT_STAGE: str = "DevelopmentalStage"


def extract_nonzero_counts(matrix_row) -> tuple[np.ndarray, np.ndarray]:
    """Extract nonzero gene indices and values from a single cell's count vector.

    Returns (gene_indices, gene_values) as np.int32 and np.float32.
    """
    if sp.issparse(matrix_row):
        row_csr = sp.csr_matrix(matrix_row)
        indices = row_csr.indices.astype(np.int32)
        values = row_csr.data.astype(np.float32)
    else:
        arr = np.asarray(matrix_row).ravel()
        nonzero_mask = arr > 0
        indices = np.where(nonzero_mask)[0].astype(np.int32)
        values = arr[nonzero_mask].astype(np.float32)
    return indices, values


def remap_and_sort_indices(
    local_indices: np.ndarray,
    local_values: np.ndarray,
    positional_to_global: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Remap positional feature indices to global indices and sort both arrays.

    Takes local (positional) indices from a single cell's sparse vector, maps them
    to global feature indices via ``positional_to_global``, and sorts both the
    indices and values arrays by the global index order.

    Returns (global_indices, sorted_values) as np.int32 and np.float32.
    """
    global_indices = positional_to_global[local_indices]
    sort_order = np.argsort(global_indices)
    return global_indices[sort_order], local_values[sort_order]


def build_additional_metadata(row, columns: list[str]) -> str | None:
    """Build additional_metadata JSON string from selected obs columns.

    Skips NaN values so different cells may have different keys.
    """
    metadata = {}
    for col in columns:
        val = row.get(col)
        if val is not None and not (isinstance(val, float) and np.isnan(val)):
            metadata[col] = str(val)
    if not metadata:
        return None
    return json.dumps(metadata)


def upsert_table(
    db: lancedb.DBConnection,
    table_name: str,
    data,
    *,
    schema=None,
) -> lancedb.table.Table:
    """Add data to an existing table or create it.

    If the table already exists, opens it and adds ``data``.  Otherwise creates
    a new table.  When ``data`` is falsy (empty list / None) and ``schema`` is
    provided, creates an empty table from the schema.

    Returns the opened or newly created table.
    """
    if table_name in db.list_tables().tables:
        table = db.open_table(table_name)
        if data:
            table.add(data)
        return table

    if data:
        return db.create_table(table_name, data=data)

    if schema is not None:
        return db.create_table(table_name, schema=schema)

    raise ValueError(f"Cannot create table '{table_name}': no data and no schema provided")


def get_max_gene_index(genes_table: lancedb.Table) -> int:
    """Get current max gene_index. Returns -1 if empty (so start_index = 0)."""
    df = genes_table.search().select(["gene_index"]).to_pandas()
    if df.empty:
        return -1
    return int(df["gene_index"].max())


def lookup_gene_indices_from_table(
    genes_table: lancedb.Table,
    organism: str,
    ensembl_ids: list[str] | None = None,
    gene_names: list[str] | None = None,
) -> dict[str, int]:
    """Look up gene_index values for Ensembl IDs already in the genes table."""
    assert (ensembl_ids is not None) ^ (gene_names is not None), (
        "Must provide either ensembl_ids or gene_names for lookup exclusively"
    )
    escaped_organism = _escape_lance_value(organism)
    if ensembl_ids:
        ensembl_ids_str = ", ".join(f"'{_escape_lance_value(eid)}'" for eid in ensembl_ids)
        where_clause = f"organism = '{escaped_organism}' AND ensembl_id IN ({ensembl_ids_str})"
        select_column = "ensembl_id"
    else:
        gene_names_str = ", ".join(f"'{_escape_lance_value(name)}'" for name in gene_names)
        where_clause = f"organism = '{escaped_organism}' AND gene_name IN ({gene_names_str})"
        select_column = "gene_name"

    df = genes_table.search().where(where_clause).select([select_column, "gene_index"]).to_pandas()
    return dict(zip(df[select_column], df["gene_index"], strict=False))


def create_new_gene_records_for_table(
    genes_table: lancedb.Table,
    organism: str,
    gene_names: set[str] | None = None,
    ensembl_ids: set[str] | None = None,
) -> None:
    assert (gene_names is not None) ^ (ensembl_ids is not None), (
        "Must provide either gene_names or ensembl_ids for lookup exclusively"
    )

    gene_records_to_add = []
    next_gene_index = get_max_gene_index(genes_table) + 1
    if gene_names is not None:
        assert isinstance(gene_names, set), "gene_names should be a set for efficient lookup"
        gene_name_mapping = lookup_gene_indices_from_table(
            genes_table, organism, gene_names=gene_names
        )
        missing_gene_names = [name for name in gene_names if name not in gene_name_mapping]
        missing_gene_name_ensembl_list = standardize_metadata_to_ontology(
            missing_gene_names,
            OntologyEntity.GENE,
            organism=organism,
            field="symbol",
            return_field="ensembl_gene_id",
        )
        missing_gene_name_ensembl = dict(
            zip(missing_gene_names, missing_gene_name_ensembl_list, strict=False)
        )
        for gene_name in missing_gene_names:
            ensembl_id = missing_gene_name_ensembl[gene_name]
            # If standardization returned the original name, it means no Ensembl ID was found
            if ensembl_id == gene_name:
                ensembl_id = None
            record = GeneSchema(
                gene_index=next_gene_index,
                gene_name=gene_name,
                ensembl_id=ensembl_id,
                ensembl_version=None,
                organism=organism,
            )
            gene_records_to_add.append(record)
            next_gene_index += 1

    if ensembl_ids is not None:
        assert isinstance(ensembl_ids, set), "ensembl_ids should be a set for efficient lookup"
        ensembl_id_mapping = lookup_gene_indices_from_table(
            genes_table, organism, ensembl_ids=ensembl_ids
        )
        missing_ensembl_ids = [eid for eid in ensembl_ids if eid not in ensembl_id_mapping]
        missing_ensembl_id_gene_names_list = standardize_metadata_to_ontology(
            missing_ensembl_ids,
            OntologyEntity.GENE,
            organism=organism,
            field="ensembl_gene_id",
            return_field="symbol",
        )
        missing_ensembl_id_gene_names = dict(
            zip(missing_ensembl_ids, missing_ensembl_id_gene_names_list, strict=False)
        )
        for ensembl_id in missing_ensembl_ids:
            gene_name = missing_ensembl_id_gene_names.get(ensembl_id) or ensembl_id
            record = GeneSchema(
                gene_index=next_gene_index,
                gene_name=gene_name,
                ensembl_id=ensembl_id,
                ensembl_version=None,
                organism=organism,
            )
            gene_records_to_add.append(record)
            next_gene_index += 1

    return gene_records_to_add


def register_genes_two_stage(
    db: lancedb.DBConnection,
    organism: str,
    *,
    measured_ensembl_ids: set[str] | None = None,
    measured_gene_names: set[str] | None = None,
    perturbation_gene_names: set[str] | None = None,
    measured_ensembl_ids_by_organism: dict[str, set[str]] | None = None,
) -> tuple[dict[str, int], dict[str, int]]:
    """Register measured genes and perturbation targets, returning lookup dicts.

    Encapsulates the two-stage gene registration pattern used by all ingestion
    scripts: first register measured genes (by Ensembl ID or gene symbol), then
    register perturbation targets (by gene name) as a separate step to avoid
    duplicates.

    Exactly one of ``measured_ensembl_ids``, ``measured_gene_names``, or
    ``measured_ensembl_ids_by_organism`` must be provided for measured genes.

    Parameters
    ----------
    db
        LanceDB connection.
    organism
        Organism string (e.g. ``"human"``).  Ignored when
        ``measured_ensembl_ids_by_organism`` is used (organisms come from the dict
        keys).
    measured_ensembl_ids
        Set of Ensembl gene IDs for measured genes (single organism).
    measured_gene_names
        Set of gene symbols for measured genes (when no Ensembl IDs available).
    perturbation_gene_names
        Set of gene symbols for perturbation targets (optional).
    measured_ensembl_ids_by_organism
        Dict mapping organism → set of Ensembl IDs for barnyard/multi-organism
        datasets.

    Returns
    -------
    measured_lookup : dict[str, int]
        Mapping from measured gene identifier (Ensembl ID or gene symbol) to
        ``gene_index``.
    perturbation_lookup : dict[str, int]
        Mapping from perturbation target gene name to ``gene_index``.
        Empty dict when ``perturbation_gene_names`` is not provided.
    """
    n_measured_args = sum(
        x is not None
        for x in [measured_ensembl_ids, measured_gene_names, measured_ensembl_ids_by_organism]
    )
    assert n_measured_args == 1, (
        "Must provide exactly one of measured_ensembl_ids, measured_gene_names, "
        "or measured_ensembl_ids_by_organism"
    )

    genes_table = upsert_table(db, "genes", None, schema=GeneSchema)

    # Stage 1: measured genes
    if measured_ensembl_ids_by_organism is not None:
        # Barnyard: register per organism
        for org, ensembl_ids in measured_ensembl_ids_by_organism.items():
            new_records = create_new_gene_records_for_table(
                genes_table, org, ensembl_ids=ensembl_ids
            )
            if new_records:
                print(f"  Registering {len(new_records)} {org} measured gene records...")
                genes_table.add(new_records)

        # Build combined lookup across all organisms
        measured_lookup: dict[str, int] = {}
        for org, ensembl_ids in measured_ensembl_ids_by_organism.items():
            measured_lookup.update(
                lookup_gene_indices_from_table(genes_table, org, ensembl_ids=ensembl_ids)
            )
    elif measured_ensembl_ids is not None:
        new_records = create_new_gene_records_for_table(
            genes_table, organism, ensembl_ids=measured_ensembl_ids
        )
        if new_records:
            print(f"  Registering {len(new_records)} measured gene records...")
            genes_table.add(new_records)
        measured_lookup = lookup_gene_indices_from_table(
            genes_table, organism, ensembl_ids=measured_ensembl_ids
        )
    else:
        new_records = create_new_gene_records_for_table(
            genes_table, organism, gene_names=measured_gene_names
        )
        if new_records:
            print(f"  Registering {len(new_records)} measured gene records...")
            genes_table.add(new_records)
        measured_lookup = lookup_gene_indices_from_table(
            genes_table, organism, gene_names=measured_gene_names
        )

    # Stage 2: perturbation targets by gene name
    perturbation_lookup: dict[str, int] = {}
    if perturbation_gene_names:
        new_records = create_new_gene_records_for_table(
            genes_table, organism, gene_names=perturbation_gene_names
        )
        if new_records:
            print(f"  Registering {len(new_records)} perturbation target gene records...")
            genes_table.add(new_records)
        perturbation_lookup = lookup_gene_indices_from_table(
            genes_table, organism, gene_names=perturbation_gene_names
        )

    return measured_lookup, perturbation_lookup


def resolve_pubchem_cids(
    names: list[str] | None = None,
    smiles: list[str] | None = None,
) -> tuple[dict[str, int], set[str]]:
    """Resolve molecule names or SMILES to PubChem CIDs via the PubChem API.

    Standalone function — does not require a LanceDB table. Intended for use
    during data preparation to validate and standardize compound identifiers
    before ingestion.

    Accepts exactly one of ``names`` or ``smiles``.

    Returns
    -------
    resolved : dict[str, int]
        Mapping from each input value to its PubChem CID (only resolved entries).
    unresolved : set[str]
        Input values for which no CID could be found.
    """
    from time import sleep

    import pubchempy as pcp

    assert (names is not None) ^ (smiles is not None), "Must provide exactly one of names or smiles"

    values = list(filter(lambda x: isinstance(x, str), set(names or smiles)))
    namespace = "name" if names is not None else "smiles"

    resolved: dict[str, int] = {}
    for value in values:
        try:
            cids = pcp.get_cids(value, namespace=namespace)
        except pcp.BadRequestError:
            cids = []
        if cids:
            resolved[value] = cids[0]
        sleep(0.2)  # PubChem rate limit: max 5 req/s

    unresolved = {v for v in values if v not in resolved}
    return resolved, unresolved


def lookup_pubchem_cids(
    molecules_table: lancedb.Table,
    names: list[str] | None = None,
    smiles: list[str] | None = None,
) -> dict[str, int]:
    """Look up molecule sample_uids by PubChem CID. Returns an empty dict if not found."""
    assert (names is not None) ^ (smiles is not None), (
        "Must provide either names or smiles for molecule lookup exclusively"
    )
    if names is not None:
        names = list(filter(lambda x: isinstance(x, str), set(names)))
        query_str = ", ".join(f"'{_escape_lance_value(name)}'" for name in names)
        field = "iupac_name"
    else:
        smiles = list(filter(lambda x: isinstance(x, str), set(smiles)))
        query_str = ", ".join(f"'{_escape_lance_value(s)}'" for s in smiles)
        field = "smiles"

    df = (
        molecules_table.search()
        .where(f"{field} IN ({query_str})")
        .select([field, "pubchem_cid"])
        .to_pandas()
    )
    field_to_cid_mapping = dict(zip(df[field], df["pubchem_cid"], strict=False))

    # Get molecules that we couldn't find CIDs for and look them
    # up with the PubChem API. This is slower because of rate limits but should
    # be needed less often as we build up the molecules table.
    missing_values = [v for v in (names or smiles) if v not in field_to_cid_mapping]
    if missing_values:
        from time import sleep

        import pubchempy as pcp

        for value in missing_values:
            if not isinstance(value, str):
                # There might be a NaN or None in the list
                continue

            result = pcp.get_cids(value, namespace=("name" if names is not None else "smiles"))
            if result:
                field_to_cid_mapping[value] = result[0]

            # Max of 5 requests per second to PubChem API to avoid rate limiting
            sleep(0.2)

    return field_to_cid_mapping


def lookup_molecule_uid(
    molecules_table: lancedb.Table,
    values: list[str] | list[int],
    field: Literal["pubchem_cid", "name", "smiles"] = "pubchem_cid",
) -> dict[str | int, str]:
    """Look up a molecule's sample_uid by PubChem CID. Returns an empty dict if not found."""
    if field == "pubchem_cid":
        # This field is numeric
        values_to_search = ", ".join(str(v) for v in values)
    else:
        # These fields are strings
        values_to_search = ", ".join(f"'{_escape_lance_value(str(v))}'" for v in values)

    df = (
        molecules_table.search()
        .where(f"{field} IN ({values_to_search})")
        .select(["sample_uid", field])
        .to_pandas()
    )
    if df.empty:
        return {}

    return dict(zip(df[field], df["sample_uid"], strict=False))


def resolve_molecule_uids_by_pubchem_cid(
    molecules_table: lancedb.Table,
    pubchem_cid: list[int] | None = None,
    name: list[str] | None = None,
    smiles: list[str] | None = None,
) -> tuple[list[str | None], list[MoleculeSchema], set]:
    """Resolve molecules to sample UIDs, creating new records as needed.

    Accepts exactly one of ``pubchem_cid``, ``name``, or ``smiles`` as a list.

    Returns
    -------
    sample_uids
        List of ``sample_uid`` strings (or ``None`` for unresolved) aligned with
        the input list.
    new_records
        List of new ``MoleculeSchema`` records. Caller must add them to the table.
    unresolved
        Set of input values that could not be resolved.
    """
    assert sum(x is not None for x in [pubchem_cid, name, smiles]) == 1, (
        "Must provide exactly one of pubchem_cid, name, or smiles for molecule lookup"
    )
    if name is not None:
        field_to_cid_mapping = lookup_pubchem_cids(molecules_table, names=name)
        cid_to_sample_uid = lookup_molecule_uid(
            molecules_table, list(field_to_cid_mapping.values()), field="pubchem_cid"
        )
    elif smiles is not None:
        field_to_cid_mapping = lookup_pubchem_cids(molecules_table, smiles=smiles)
        cid_to_sample_uid = lookup_molecule_uid(
            molecules_table, list(field_to_cid_mapping.values()), field="pubchem_cid"
        )
    else:
        field_to_cid_mapping = {cid: cid for cid in pubchem_cid}
        cid_to_sample_uid = lookup_molecule_uid(molecules_table, pubchem_cid, field="pubchem_cid")

    for cid in field_to_cid_mapping.values():
        if cid not in cid_to_sample_uid:
            cid_to_sample_uid[cid] = None

    new_cids = [cid for cid, uid in cid_to_sample_uid.items() if uid is None]
    new_records = []
    if new_cids:
        import pubchempy as pcp

        for compound in pcp.get_compounds(new_cids, namespace="cid"):
            record = MoleculeSchema(
                pubchem_cid=compound.cid,
                iupac_name=compound.iupac_name,
                smiles=compound.connectivity_smiles,
            )
            new_records.append(record)
            cid_to_sample_uid[compound.cid] = record.sample_uid

    # Convert the original input values to sample_uids using the conversion mapping
    if pubchem_cid is not None:
        sample_uids = [cid_to_sample_uid.get(field_to_cid_mapping.get(v)) for v in pubchem_cid]
        unresolved = set(
            [v for v, uid in zip(pubchem_cid, sample_uids, strict=False) if uid is None]
        )
    elif name is not None:
        sample_uids = [cid_to_sample_uid.get(field_to_cid_mapping.get(v)) for v in name]
        unresolved = set([v for v, uid in zip(name, sample_uids, strict=False) if uid is None])
    else:
        sample_uids = [cid_to_sample_uid.get(field_to_cid_mapping.get(v)) for v in smiles]
        unresolved = set([v for v, uid in zip(smiles, sample_uids, strict=False) if uid is None])

    return sample_uids, new_records, unresolved


def build_positional_to_gene_index(
    gene_names_or_ensembl_ids: list[str],
    gene_index_lookup: dict[str, int],
) -> np.ndarray:
    """Build measured_gene_expression_indices bytes for DatasetSchema.

    Maps gene names or Ensembl IDs to gene_index values, preserving order.
    """
    indices = np.array(
        [gene_index_lookup[gene] for gene in gene_names_or_ensembl_ids],
        dtype=np.int32,
    )
    return indices


def register_image_features(
    db: lancedb.DBConnection,
    feature_names: list[str],
) -> dict[str, int]:
    """Register image features and return name -> feature_index mapping."""
    table_names = db.list_tables().tables
    if "image_features" in table_names:
        table = db.open_table("image_features")
        existing_df = table.search().select(["feature_name", "feature_index"]).to_pandas()
        existing_map = dict(
            zip(existing_df["feature_name"], existing_df["feature_index"], strict=False)
        )
        next_index = max(existing_map.values()) + 1 if existing_map else 0
    else:
        existing_map = {}
        next_index = 0
        table = None

    new_records = []
    for name in feature_names:
        if name not in existing_map:
            existing_map[name] = next_index
            new_records.append(
                ImageFeatureSchema(
                    feature_index=next_index,
                    feature_name=name,
                    description=None,
                )
            )
            next_index += 1

    if new_records:
        if table is None:
            db.create_table("image_features", data=new_records)
        else:
            table.add(new_records)
        print(f"  Registered {len(new_records)} new image features (total: {next_index})")
    else:
        print(f"  All {len(feature_names)} features already registered")

    return existing_map


def write_cell_batch(
    db: lancedb.DBConnection,
    modality_batches: dict[str, list[CellDataRecord]],
) -> None:
    """Write a batch of cell records to modality tables.

    Parameters
    ----------
    db
        LanceDB connection.
    modality_batches
        Dict mapping table name to list of modality records.
        E.g. ``{"gene_expression": [GeneExpressionRecord(...), ...]}``
    """
    for table_name, records in modality_batches.items():
        if records:
            upsert_table(db, table_name, records)


def _get_dataset_uids_for_table(
    db: lancedb.DBConnection,
    table_name: str,
) -> list[str]:
    """Return all dataset_uids whose feature_space matches a data table."""
    feature_space = "gene_expression" if table_name == "gene_expression" else "image_features"
    df = (
        db.open_table("datasets")
        .search()
        .where(f"feature_space = '{feature_space}'")
        .select(["dataset_uid"])
        .to_pandas()
    )
    return df["dataset_uid"].unique().tolist()


def find_datasets_by_molecule(
    db: lancedb.DBConnection,
    mol_uid: str,
    table_name: str = "gene_expression",
) -> list[dict]:
    """Find all datasets containing cells treated with a given molecule.

    Iterates over each dataset and checks for cells whose
    ``chemical_perturbation_uid`` list contains ``mol_uid``.

    Returns a list of dicts with ``dataset_uid`` and ``cell_count``.
    """
    data_table = db.open_table(table_name)
    results = []

    for ds_uid in _get_dataset_uids_for_table(db, table_name):
        escaped = _escape_lance_value(ds_uid)
        cells = (
            data_table.search()
            .where(f"dataset_uid = '{escaped}' AND is_control = false")
            .select(["chemical_perturbation_uid"])
            .to_pandas()
        )
        count = sum(
            1 for uids in cells["chemical_perturbation_uid"]
            if uids is not None and mol_uid in uids
        )
        if count > 0:
            results.append({"dataset_uid": ds_uid, "cell_count": count})

    return results


def find_datasets_by_gene(
    db: lancedb.DBConnection,
    gene_index: int,
    table_name: str = "gene_expression",
) -> list[dict]:
    """Find all datasets containing cells with a genetic perturbation targeting a gene.

    Iterates over each dataset and checks for cells whose
    ``genetic_perturbation_gene_index`` list contains ``gene_index``.

    Returns a list of dicts with ``dataset_uid`` and ``cell_count``.
    """
    data_table = db.open_table(table_name)
    results = []

    for ds_uid in _get_dataset_uids_for_table(db, table_name):
        escaped = _escape_lance_value(ds_uid)
        cells = (
            data_table.search()
            .where(f"dataset_uid = '{escaped}' AND is_control = false")
            .select(["genetic_perturbation_gene_index"])
            .to_pandas()
        )
        count = sum(
            1 for gids in cells["genetic_perturbation_gene_index"]
            if gids is not None and gene_index in gids
        )
        if count > 0:
            results.append({"dataset_uid": ds_uid, "cell_count": count})

    return results


def get_cells_for_molecule(
    db: lancedb.DBConnection,
    mol_uid: str,
    dataset_uid: str,
    table_name: str = "gene_expression",
    include_controls: bool = True,
) -> pd.DataFrame:
    """Get all cells for a molecule perturbation from a specific dataset.

    Returns treated cells filtered to ``mol_uid``. If ``include_controls``,
    also appends control cells with a ``_is_treated`` column.
    """
    data_table = db.open_table(table_name)
    escaped = _escape_lance_value(dataset_uid)

    treated = (
        data_table.search()
        .where(f"dataset_uid = '{escaped}' AND is_control = false")
        .to_pandas()
    )
    treated = treated[
        treated["chemical_perturbation_uid"].apply(
            lambda uids: uids is not None and mol_uid in uids
        )
    ]

    if include_controls:
        controls = (
            data_table.search()
            .where(f"dataset_uid = '{escaped}' AND is_control = true")
            .to_pandas()
        )
        treated["_is_treated"] = True
        controls["_is_treated"] = False
        return pd.concat([treated, controls], ignore_index=True)

    return treated


def standardize_metadata_to_ontology(
    values: list[str] | np.ndarray,
    entity: OntologyEntity,
    field: str,
    organism: str | None = None,
    return_field: str | None = None,
) -> list[str]:
    """
    Takes a list of metadata values and tries to standardize them to a public
    ontology based on the entity type. Returns standardized values when possible,
    otherwise it returns the original value.
    """
    import bionty as bt

    entity_str = entity.value
    if entity_str in ["CellLine", "CellType", "Tissue", "Disease"] or entity_str is None:
        organism = "all"

    ontology = getattr(bt, entity.value).public(organism=organism or "all")
    standard_values = ontology.standardize(values, field=field, return_field=return_field)
    return standard_values


def validate_metadata_against_ontology(
    values: list[str] | np.ndarray,
    entity: OntologyEntity,
    field: str,
    organism: str | None = None,
    return_field: str | None = None,
) -> list[str]:
    """
    Takes a list of metadata values and validates them against a public ontology after
    standardization. Returns a list of values that failed validation. This function
    should be used to make sense that values will resolve correctly before adding
    them to records.
    """
    import bionty as bt

    entity_str = entity.value
    if entity_str in ["CellLine", "CellType", "Tissue", "Disease"] or entity_str is None:
        organism = "all"

    ontology = getattr(bt, entity.value).public(organism=organism or "all")
    standard_values = ontology.standardize(values, field=field, return_field=field)
    validated = ontology.validate(standard_values, field=field)

    return np.array(standard_values)[~validated].tolist()


def search_metadata_in_ontology(
    query: str,
    entity: OntologyEntity,
    organism: str | None = None,
) -> pd.DataFrame:
    """
    Searches a query term in the specified ontology and returns a dataframe of results.
    This can be used to inspect why certain metadata values may not be validating or standardizing
    correctly and map them to the closest valid term in the ontology.
    """
    import bionty as bt

    entity_str = entity.value
    if entity_str in ["CellLine", "CellType", "Tissue", "Disease"] or entity_str is None:
        organism = "all"

    ontology = getattr(bt, entity.value).public(organism=organism or "all")
    return ontology.search(query)
