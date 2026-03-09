---
name: lancedb-query
description: Query the project's LanceDB database containing curated perturbation biology publications, datasets, gene expression records, and molecule/gene registries. Use SQL WHERE clauses, full-text search, and convenience functions to retrieve cells, reconstruct AnnData objects, and load publication context for downstream analysis.
---

# LanceDB Query

## Purpose
Query the project's LanceDB database containing curated perturbation biology publications, datasets, gene expression records, and molecule/gene registries. This is the primary structured data source for the pipeline.

## When to Use
- `paper-search-workflow` calls this skill as **Source C** to search publications and datasets
- Any workflow needing to look up gene indices, molecule UIDs, or dataset metadata
- Retrieving cell-level expression data for a specific dataset or perturbation condition
- Loading data + publication context for downstream analysis by agents

## Connection

```python
import lancedb
from ych.atlas_search import DB_URI

db = lancedb.connect(DB_URI)
```

## Database Schema

### Core Tables

#### `publications` — Paper sections (denormalized)
| Column | Type | Notes |
|--------|------|-------|
| `pmid` | `str` | PubMed ID |
| `doi` | `str` | DOI |
| `title` | `str` | Paper title |
| `journal` | `str` | Journal name |
| `publication_date` | `datetime` | Publication date |
| `section_title` | `str` | e.g. "Abstract", "Methods", "Results" |
| `section_text` | `str` | Full text of the section |

**Indexes**: Scalar on `pmid`, `doi`, `journal`, `section_title`.

One row per section. Metadata is repeated per row (denormalized).

#### `datasets` — Dataset metadata
| Column | Type | Notes |
|--------|------|-------|
| `pmid` | `str \| None` | Links to publication |
| `doi` | `str \| None` | |
| `cell_count` | `int` | Total cells |
| `feature_space` | `str` | `"gene_expression"` or `"image_features"` |
| `measured_feature_indices` | `bytes \| None` | Sparse indices into feature tables |
| `accession_database` | `str \| None` | "GEO", "ArrayExpress", etc. |
| `accession_id` | `str \| None` | "GSE12345" |
| `dataset_description` | `str \| None` | Protocol/experimental description |
| `dataset_uid` | `str` | Unique ID (auto-generated UUID) |

**Indexes**: Scalar on `pmid`, `doi`, `feature_space`, `accession_database`, `accession_id`, `dataset_uid`. FTS on `dataset_description`.

#### `gene_expression` — Per-cell sparse counts
| Column | Type | Notes |
|--------|------|-------|
| `cell_uid` | `str` | Unique cell ID |
| `dataset_uid` | `str` | Links to datasets table |
| `assay` | `str` | e.g. "10x Chromium v3" |
| `gene_indices` | `bytes` | Sparse column indices (int32) |
| `counts` | `bytes` | Sparse counts (float32) |
| `is_control` | `bool \| None` | |
| `chemical_perturbation_uid` | `list[str] \| None` | Links to molecules.sample_uid |
| `chemical_perturbation_concentration` | `list[float] \| None` | |
| `genetic_perturbation_gene_index` | `list[int] \| None` | Links to genes.gene_index |
| `genetic_perturbation_method` | `list[str] \| None` | CRISPR-cas9, CRISPRi, siRNA, etc. |
| `perturbation_search_string` | `str` | Auto-generated: `"SM:<uid> GENE_ID:<idx> METHOD:<m>"` |

### Reference Tables

#### `genes` — Global gene registry
| Column | Type | Notes |
|--------|------|-------|
| `gene_index` | `int` | Unique sequential ID (positional) |
| `gene_name` | `str` | e.g. "TP53" |
| `ensembl_id` | `str \| None` | e.g. "ENSG00000141510" |
| `organism` | `str` | "human", "mouse" |

#### `molecules` — Chemical compound registry
| Column | Type | Notes |
|--------|------|-------|
| `smiles` | `str \| None` | SMILES string |
| `pubchem_cid` | `int \| None` | PubChem Compound ID |
| `iupac_name` | `str \| None` | Standardized name |
| `sample_uid` | `str` | Auto-generated UUID |

#### `image_feature_vectors` — Per-cell image features
Same metadata columns as `gene_expression`, plus `feature_values: bytes` (dense float32 vector).

#### `image_features` — Feature name registry
| Column | Type | Notes |
|--------|------|-------|
| `feature_index` | `int` | |
| `feature_name` | `str` | |
| `description` | `str \| None` | |

---

## Query API (Preferred)

Use `ych.atlas_search` functions. These accept **raw SQL WHERE clauses** — write the SQL based on the user's question rather than constructing an `AtlasQuery` dataclass.

```python
from ych.atlas_search import (
    DB_URI,
    query_cells,
    cells_to_anndata,
    fetch_dataset_context,
    save_query_results,
)
import lancedb

db = lancedb.connect(DB_URI)
```

### 1. Query Cells with SQL WHERE

```python
# All cells from a specific dataset
cells = query_cells(db, "gene_expression",
    where="dataset_uid = 'abc-123'")

# Control cells only
controls = query_cells(db, "gene_expression",
    where="dataset_uid = 'abc-123' AND is_control = true")

# Cells from multiple datasets
cells = query_cells(db, "gene_expression",
    where="dataset_uid IN ('uid1', 'uid2', 'uid3')")

# Filter by assay type
cells = query_cells(db, "gene_expression",
    where="assay = '10x Chromium v3'")
```

### 2. Find Datasets by Perturbation (Preferred)

The simplest way to find datasets for a specific perturbation is with the
utility functions in `ych.ingestion_utils`. These iterate over all datasets
and check each one — slower than FTS but reliable and correct.

```python
from ych.ingestion_utils import (
    find_datasets_by_molecule,
    find_datasets_by_gene,
    get_cells_for_molecule,
    resolve_pubchem_cids,
    lookup_molecule_uid,
)

# Find datasets for a chemical perturbation
resolved, _ = resolve_pubchem_cids(names=["Abexinostat"])
mol_table = db.open_table("molecules")
cid_to_uid = lookup_molecule_uid(mol_table, list(resolved.values()), field="pubchem_cid")
mol_uid = cid_to_uid[resolved["Abexinostat"]]

datasets = find_datasets_by_molecule(db, mol_uid)
# [{"dataset_uid": "abc-123", "cell_count": 4505}, ...]

# Find datasets for a genetic perturbation
genes = db.open_table("genes")
tp53 = genes.search().where("gene_name = 'TP53' AND organism = 'human'").to_pandas()
gene_idx = int(tp53["gene_index"].iloc[0])

datasets = find_datasets_by_gene(db, gene_idx)

# Get all treated + control cells for a molecule from a specific dataset
cells_df = get_cells_for_molecule(db, mol_uid, dataset_uid="abc-123")
# Returns DataFrame with _is_treated column (True for treated, False for controls)
```

### 3. Full-Text Search for Perturbations (Advanced)

The `perturbation_search_string` column contains tokens like `GENE_ID:42 METHOD:CRISPR-cas9 SM:<molecule_uid>`. Use FTS via `query_cells` to search it. Under the hood, `query_cells` builds a LanceDB `MatchQuery` with the specified `fts_operator` (`"OR"` or `"AND"`).

**IMPORTANT:** Always use FTS (via `query_cells` with `fts_query`) to search
perturbation data — do NOT use SQL WHERE on `perturbation_search_string`.
FTS uses the LanceDB `MatchQuery` which supports AND/OR operators and is
indexed for fast retrieval.

**Molecule UID hyphens:** The FTS index tokenizer splits on hyphens and colons.
To prevent UUID fragmentation, `perturbation_search_string` stores molecule UIDs
with hyphens stripped: `SM:cb4125bfd63648fd86aa41a4739139ff`. When building FTS
queries for molecules, **always strip hyphens** from the UUID:
`f"SM:{mol_uid.replace('-', '')}"`. The `build_search_query` helper in
`atlas_search.py` does this automatically for `AtlasQuery` usage, but when
calling `query_cells` with a raw `fts_query` string you must strip manually.

```python
import polars as pl

# OR (default): cells with TP53 OR BRCA1 perturbation (by gene_index)
cells = query_cells(db, "gene_expression",
    fts_query="GENE_ID:42 GENE_ID:107",
    fts_operator="OR",
    limit=100_000)

# AND: cells with BOTH a specific gene AND a specific method
cells = query_cells(db, "gene_expression",
    fts_query="GENE_ID:42 METHOD:CRISPR-cas9",
    fts_operator="AND",
    limit=100_000)

# Combine FTS with SQL WHERE
cells = query_cells(db, "gene_expression",
    fts_query="GENE_ID:42",
    where="is_control = false",
    fts_operator="AND",
    limit=100_000)

# Search by molecule UID — strip hyphens to match indexed tokens
cells = query_cells(db, "gene_expression",
    fts_query=f"SM:{mol_uid.replace('-', '')}",
    fts_operator="OR",
    limit=100_000)
```

**Note:** FTS results include a `_score` column. Drop it before combining with
non-FTS results: `pl.from_arrow(cells).drop("_score")`

**To find gene indices for FTS**, look up genes first:
```python
genes = db.open_table("genes")
tp53 = genes.search().where("gene_name = 'TP53' AND organism = 'human'").to_pandas()
gene_idx = tp53["gene_index"].iloc[0]
# Then use f"GENE_ID:{gene_idx}" in fts_query
```

**To find molecule UIDs for chemical perturbation FTS**, always resolve the
user-provided compound name through PubChem first, then look up the molecule
`sample_uid` in the database:
```python
from ych.ingestion_utils import resolve_pubchem_cids, lookup_molecule_uid

# Step 1: Resolve compound name → PubChem CID via PubChem API
resolved, unresolved = resolve_pubchem_cids(names=["vorinostat"])
# resolved = {"vorinostat": 5311}

# Step 2: Look up sample_uid in the molecules table by PubChem CID
molecules_table = db.open_table("molecules")
cid_to_uid = lookup_molecule_uid(molecules_table, list(resolved.values()), field="pubchem_cid")
# cid_to_uid = {5311: "abc-molecule-uid"}

# Step 3: Use the sample_uid in FTS query (strip hyphens to match index)
mol_uid = cid_to_uid[resolved["vorinostat"]]
cells = query_cells(db, "gene_expression",
    fts_query=f"SM:{mol_uid.replace('-', '')}",
    fts_operator="OR",
    limit=100_000)
```

This three-step resolution (compound name → PubChem CID → molecule sample_uid →
FTS on `SM:<uid>`) is required because the `perturbation_search_string` stores
molecule `sample_uid`s, not compound names or CIDs directly.

### 4. Use `limit` to Control Result Size

**Always set `limit`** — queries without a limit can return millions of rows.

**IMPORTANT for FTS queries:** LanceDB FTS returns results ranked by relevance
score and may not return all matches if the limit is too low. Set `limit` to
at least `100_000` for FTS queries to ensure all datasets are covered.

```python
# Molecule FTS — strip hyphens from UID to match indexed tokens
treated = query_cells(db, "gene_expression",
    fts_query=f"SM:{mol_uid.replace('-', '')}",
    fts_operator="OR",
    limit=100_000)

# For targeted queries where you know the dataset, smaller limits are fine
controls = query_cells(db, "gene_expression",
    where="dataset_uid = 'abc' AND is_control = true",
    limit=5000)
```

### 5. Reconstruct AnnData from Query Results

```python
# Query cells
cells = query_cells(db, "gene_expression",
    where="dataset_uid = 'abc-123'",
    limit=10000)

# Reconstruct into AnnData objects (one per dataset_uid)
anndatas = cells_to_anndata(db, cells, feature_space="gene_expression")
# Returns: {"abc-123": AnnData(n_obs x n_vars)}

# For image features
img_cells = query_cells(db, "image_feature_vectors",
    where="dataset_uid = 'abc-123'")
img_anndatas = cells_to_anndata(db, img_cells, feature_space="image_features")
```

The returned AnnData objects have:
- `adata.X`: sparse CSR matrix (gene expression) or dense ndarray (image features)
- `adata.obs`: cell metadata including perturbation info, `is_control`, `assay`, `dataset_uid`
- `adata.var`: gene Ensembl IDs (gene expression) or feature names (image features)

### 6. Load Publication + Dataset Context

Fetch the full text of associated publications and dataset descriptions to pass to an LLM agent for analysis.

```python
# Get dataset_uids from your query results
dataset_uids = list(anndatas.keys())

# Fetch context: dataset metadata + full publication text
context = fetch_dataset_context(db, dataset_uids)
# Returns list of dicts:
# [
#   {
#     "dataset_uid": "abc-123",
#     "pmid": "12345678",
#     "doi": "10.1234/...",
#     "cell_count": 50000,
#     "feature_spaces": ["gene_expression"],
#     "accession_id": "GSE12345",
#     "dataset_description": "...",
#     "publication": {
#       "title": "...",
#       "journal": "Nature",
#       "publication_date": "2024-01-15",
#       "sections": [
#         {"title": "Abstract", "text": "..."},
#         {"title": "Methods", "text": "..."},
#         ...
#       ]
#     }
#   }
# ]
```

### 7. Save Results to Disk

Save AnnData files and context JSON for downstream agent consumption.

```python
manifest = save_query_results(
    output_dir="results/my_query",
    anndatas=anndatas,
    context=context,
)
# manifest = {
#   "output_dir": "results/my_query",
#   "h5ad_files": {"abc-123": "results/my_query/abc-123.h5ad"},
#   "context_file": "results/my_query/context.json",
# }
```

### 8. Complete Workflow Example

```python
import lancedb
from ych.atlas_search import (
    DB_URI, query_cells, cells_to_anndata,
    fetch_dataset_context, save_query_results,
)

db = lancedb.connect(DB_URI)

# Step 1: Find the gene index for TP53
genes = db.open_table("genes")
tp53 = genes.search().where("gene_name = 'TP53' AND organism = 'human'").to_pandas()
gene_idx = int(tp53["gene_index"].iloc[0])

# Step 2: Find CRISPR knockouts of TP53 (high limit to get all datasets)
cells = query_cells(db, "gene_expression",
    fts_query=f"GENE_ID:{gene_idx} METHOD:CRISPR-cas9",
    fts_operator="AND",
    limit=100_000)

# Step 3: Reconstruct AnnData
anndatas = cells_to_anndata(db, cells)

# Step 4: Load publication + dataset context
context = fetch_dataset_context(db, list(anndatas.keys()))

# Step 5: Save everything
manifest = save_query_results("results/tp53_crispr", anndatas, context)
```

---

## Direct Table Queries (SQL WHERE Reference)

For querying `publications`, `datasets`, `genes`, and `molecules` tables directly (not through `query_cells`), use LanceDB's `.search().where()` pattern.

### SQL WHERE Syntax

LanceDB WHERE clauses support standard SQL operators:

```python
table = db.open_table("datasets")

# Equality
table.search().where("accession_id = 'GSE12345'").to_pandas()

# IN clause
table.search().where("accession_id IN ('GSE12345', 'GSE67890')").to_pandas()

# AND / OR
table.search().where("feature_space = 'gene_expression' AND cell_count > 1000").to_pandas()

# LIKE (pattern matching)
table.search().where("accession_id LIKE 'GSE%'").to_pandas()

# IS NULL / IS NOT NULL
table.search().where("pmid IS NOT NULL").to_pandas()

# Comparison operators
table.search().where("cell_count >= 5000 AND cell_count <= 50000").to_pandas()
```

### Search Publications (FTS on section_text)

```python
pubs = db.open_table("publications")

# Substring search on section_text (downloads full table, ~1K rows — OK)
hits = search_text(db, "publications", "section_text", "CRISPR screen K562")

# FTS + filter by section
methods = pubs.search("10x Chromium", query_type="fts") \
    .where("section_title = 'Methods'").limit(10).to_pandas()

# FTS on dataset descriptions
datasets = db.open_table("datasets")
hits = datasets.search("perturbation screen", query_type="fts") \
    .where("feature_space = 'gene_expression'").limit(10).to_pandas()
```

### Look Up Genes

```python
genes = db.open_table("genes")

# By name
genes.search().where("gene_name = 'TP53'").to_pandas()

# Multiple genes
genes.search().where("gene_name IN ('TP53', 'BRCA1', 'MYC')").to_pandas()

# By Ensembl ID
genes.search().where("ensembl_id = 'ENSG00000141510'").to_pandas()
```

### Look Up Molecules

```python
molecules = db.open_table("molecules")

# By PubChem CID
molecules.search().where("pubchem_cid = 5311").to_pandas()

# By name
molecules.search().where("iupac_name = 'vorinostat'").to_pandas()
```

---

## Legacy API: AtlasQuery

The `AtlasQuery` dataclass is still available for programmatic use but is **not recommended** for agent workflows. Prefer writing SQL WHERE clauses directly via `query_cells`.

```python
from ych.atlas_search import AtlasQuery, create_anndatas_from_query

query = AtlasQuery(
    dataset_uid="abc-123",
    gene_names=["TP53"],
    perturbation_method="CRISPR-cas9",
    is_control=False,
)
results = create_anndatas_from_query(db, query, max_records=10000)
```

## Dependencies
- Uses: `src/ych/schema.py` (table schemas), `src/ych/ingestion_utils.py` (helpers)
- Used by: `paper-search-workflow` (Source C), `dataset-preprocessing-workflow`, `perturbation-type-router`
