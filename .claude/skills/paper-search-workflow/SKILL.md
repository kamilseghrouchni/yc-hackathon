---
name: paper-search-workflow
description: Searches for perturbation biology papers and datasets in the curated LanceDB database. Use when a user asks to "find papers", "search for datasets", "look up perturb-seq studies", or needs to retrieve publications about CRISPR screens, drug perturbations, or single-cell perturbation experiments.
metadata:
    skill-author: K-Dense Inc.
---

# Paper Search Workflow

## Purpose
Searches for perturbation biology papers and datasets in the curated LanceDB database. Takes a structured query (from `query-understanding-workflow`) and returns a ranked candidate list with metadata.

## When to Use
Invoke after `query-understanding-workflow` produces a structured query. This is the primary data retrieval step before assessment.

## IMPORTANT: LanceDB is the ONLY data source
Do NOT query external APIs (Semantic Scholar, EuropePMC, PubMed, etc.). All searches go through the curated LanceDB database, which contains pre-ingested publications, datasets, gene expression records, and molecule/gene registries. This ensures results are high-signal, curated, and have resolved identifiers.

## Workflow Steps

### Step 1: Prepare Search Queries
From the structured query object, build search terms for LanceDB:

- **Primary terms**: Combine entities (genes, drugs, cell types) with perturbation type keywords
- **Boolean logic**: Use AND between entity categories, OR within categories
- **Filters**: Apply organism, year range, and data availability filters

Example for query `{genes: ["KRAS"], cell_types: ["A549"], perturbation_type: "genetic_crispr"}`:
```
("KRAS" AND "A549") AND ("CRISPR" OR "knockout" OR "Cas9")
```

### Step 2: Query LanceDB
Query the curated LanceDB database for publications and datasets. Data has been ingested, genes resolved via `gene-resolver`, and molecules standardized via `molecule-resolver`.

> **No FTS on S3** — Never use `query_type="fts"`. Use pandas `str.contains()` or scalar `.where()` filters.

**Connection:**
```python
import sys; sys.path.insert(0, "scripts")
from db_connect import get_db, search_text

db = get_db()
```

**Publication search** (pandas text filter on `section_text`):
```python
# search_text() calls .to_pandas() then str.contains() — safe for small tables
hits = search_text(db, "publications", "section_text", "<query_terms>")

# Or manual pandas for more complex filters:
pubs_df = db.open_table("publications").to_pandas()
results = pubs_df[
    pubs_df["section_text"].str.contains("<query_terms>", case=False, na=False)
]
```

**Dataset search** (scalar `.where()` + pandas text filter):
```python
datasets = db.open_table("datasets")
# By accession (scalar filter — works on S3)
ds = datasets.search().where("accession_id = 'GSE12345'").to_pandas()

# By text on dataset_description (small table — pandas OK)
ds_df = datasets.to_pandas()
hits = ds_df[ds_df["dataset_description"].str.contains("<query_terms>", case=False, na=False)]
```

**Perturbation-aware search** (gene expression — LARGE table, always filter first):
```python
gene_expr = db.open_table("gene_expression")
# Use scalar .where() with LIKE — never call .to_pandas() without filter
cells = gene_expr.search().where(
    "perturbation_search_string LIKE '%GENE_ID:<gene_index>%'"
).limit(100).to_pandas()
```

**Cross-reference publications → datasets** (join on `pmid`):
```python
pubs_df = db.open_table("publications").to_pandas()
ds_df   = db.open_table("datasets").to_pandas()

# Find papers matching query, then get their datasets
paper_pmids = pubs_df[
    pubs_df["title"].str.contains("<query>", case=False, na=False)
]["pmid"].unique()

related_datasets = ds_df[ds_df["pmid"].isin(paper_pmids)]
```

Prioritize LanceDB results over API results when available (curated > crawled).

See `lancedb-query` SKILL.md for full schema and query patterns.

### Step 3: Enrich with Data Availability
For each candidate paper, check if associated datasets exist:
- Look for GEO accession numbers (GSE*) in abstract/text
- Check for links to data repositories
- Flag papers with downloadable scRNA-seq or bulk RNA-seq data

### Step 4: Rank Candidates
Initial ranking (before quality assessment):
1. **Data availability** — papers with deposited data rank higher
2. **Citation count** — normalized by year (citations per year)
3. **Recency** — recent papers weighted slightly higher
4. **Query match** — how many query entities appear in title/abstract

### Step 5: Return Candidate List

```json
{
  "query_used": "<structured query object>",
  "sources_searched": ["lancedb"],
  "sources_unavailable": [],
  "total_results": "<number>",
  "candidates": [
    {
      "rank": 1,
      "paper_id": "<DOI>",
      "title": "<paper title>",
      "authors": ["<first author et al.>"],
      "year": 2024,
      "abstract": "<abstract text>",
      "perturbation_type": "<chemical|genetic_crispr|genetic_rnai|combinatorial>",
      "organism": "<species>",
      "cell_types": ["<cell types mentioned>"],
      "data_accessions": ["GSE12345"],
      "data_available": true,
      "citation_count": 45,
      "source": "lancedb",
      "open_access": true
    }
  ],
  "search_metadata": {
    "timestamp": "<ISO 8601>",
    "query_terms": ["<search terms used>"],
    "filters_applied": {}
  }
}
```

## Error Handling
- If LanceDB is unavailable, return an error explaining the database is not connected
- If no results found, broaden search by dropping least specific terms
- Return empty candidate list with explanation if the query matches nothing

## Dependencies
- Uses: `query-understanding-workflow` (for structured query input)
- Used by: `concurrent-assessment-workflow` (passes candidates for assessment)
- Uses: `lancedb-query` (sole data source — curated DB with gene/molecule resolution)
