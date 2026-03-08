---
name: output-routing-workflow
description: Routes pipeline output to one of 3 delivery modes — download file, persist as API endpoint, or continue in-agent processing. Use as the final pipeline step after preprocessing and validation, or when a user says "save results", "download dataset", "create endpoint", or "analyze further".
metadata:
    skill-author: K-Dense Inc.
---

# Output Routing Workflow

## Purpose
Routes the final output of the pipeline to one of three explicit delivery modes. After preprocessing and validation, the user chooses how to receive their processed data.

## When to Use
Invoke as the final step of the pipeline, after `dataset-preprocessing-workflow` and `result-schema-validator` have completed successfully.

## Delivery Modes

### Mode A: Download

Save processed data as files for the user to download.

**Trigger**: User says "download", "export", "save to disk", "give me the file", or selects download in UI.

**Supported formats**:
- `.h5ad` (default) — full processed AnnData object
- `.csv` — DE results table
- `.json` — summary statistics and metadata

**Actions**:
1. Save processed h5ad to `data/processed/<dataset_name>.h5ad`
2. Optionally export DE results as CSV to `data/processed/<dataset_name>_de.csv`
3. Optionally export summary as JSON to `data/processed/<dataset_name>_summary.json`
4. Invoke `downstream-agent-skills-generator` to create dataset SKILL.md
5. Return file path(s) to user

**Output to user**:
```
Download ready:
  - h5ad: data/processed/<name>.h5ad (42 MB, 15000 cells x 2000 genes)
  - CSV:  data/processed/<name>_de.csv (1.2 MB, 3 perturbations)
  - Skill: .claude/skills/datasets/<name>/SKILL.md

Status: download ready
```

### Mode B: API Endpoint

Persist the processed dataset into LanceDB and register a queryable REST endpoint.

**Trigger**: User says "create endpoint", "make accessible via API", "serve this", "persist to database", or selects API mode in UI.

**Actions**:
1. Persist processed dataset back into LanceDB:
   - Dataset metadata → `datasets` table
   - DE results → queryable format in `de_results` table
   - Cell embeddings (PCA/UMAP coordinates) → `embeddings` table
   - Cluster assignments + perturbation labels → `cell_metadata` table
2. Register dataset metadata (accession, cell count, gene count, perturbations, preprocessing params)
3. Return endpoint spec to user
4. Invoke `downstream-agent-skills-generator` for discoverability

**Endpoint spec**:
```
GET /api/datasets/<dataset_id>
GET /api/datasets/<dataset_id>/de?perturbation=<name>&min_log2fc=1&max_padj=0.05
GET /api/datasets/<dataset_id>/cells?cluster=<id>&perturbation=<name>
GET /api/datasets/<dataset_id>/embeddings?type=umap
```

**Output to user**:
```
API endpoint created:
  Dataset ID: ds_kras_a549_20260308
  Endpoint:   GET /api/datasets/ds_kras_a549_20260308

  Available query params:
    /de?perturbation=KRAS_G12C&min_log2fc=1&max_padj=0.05
    /cells?cluster=3&perturbation=KRAS_G12C
    /embeddings?type=umap

  Example:
    curl http://localhost:8000/api/datasets/ds_kras_a549_20260308/de?perturbation=KRAS_G12C

  Stored in LanceDB: datasets, de_results, embeddings, cell_metadata tables
```

### Mode C: Continue Processing

Keep data in-agent and proceed to the next analysis step without exporting.

**Trigger**: User says "analyze further", "run enrichment", "show UMAP", "compare perturbations", or selects continue in UI.

**Actions**:
1. Keep h5ad file path as working reference (no export step)
2. Present menu of available next analysis steps:
   - **Pathway enrichment** on top DE genes (routes to enrichment analysis)
   - **UMAP visualization** colored by perturbation/cluster (routes to scanpy visualization)
   - **Perturbation comparison** — X vs Y head-to-head (routes to comparative analysis)
   - **Cell type annotation** using marker genes or reference atlas (routes to annotation workflow)
   - **Dose-response analysis** if multiple doses present (routes to dose-response workflow)
3. Route to appropriate downstream skill based on user selection

**Output to user**:
```
Dataset ready for analysis (15000 cells x 2000 genes, 3 perturbations).

What would you like to do next?
  1. Run pathway enrichment on top DE genes
  2. Show UMAP colored by perturbation
  3. Compare perturbation X vs Y in detail
  4. Annotate cell types
  5. Dose-response analysis (if applicable)

Select an option or describe what you'd like to analyze.
```

## Routing Decision Logic

```
IF user pre-selected a mode (from UI or explicit statement):
    → Use that mode directly

IF user said "download" / "export" / "save to disk":
    → Mode A (Download)

IF user said "create endpoint" / "API" / "serve" / "persist":
    → Mode B (API Endpoint)

IF user said "analyze further" / "enrichment" / "UMAP" / "compare":
    → Mode C (Continue Processing)

IF ambiguous:
    → Present 3 options as a choice card in UI

DEFAULT (no user preference):
    → Mode C (Continue Processing) — keep the user in the flow
```

## Error Handling
- If file save fails (permissions, disk space) → report error, offer alternative format or path
- If LanceDB persistence fails → fall back to Mode A (download), note DB issue
- If skill generation fails → complete the primary mode anyway, note skill can be retried
- If downstream analysis routing fails → present the menu again with error context

## Dependencies
- **Requires**: `dataset-preprocessing-workflow` (processed data), `result-schema-validator` (validation pass)
- **Uses**: `downstream-agent-skills-generator` (Modes A & B), `lancedb-query` (Mode B)
- **Routes to**: downstream analysis skills (Mode C)
- **Terminal**: Yes — this is the final orchestration step of the pipeline
