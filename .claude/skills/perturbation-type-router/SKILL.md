---
name: perturbation-type-router
<<<<<<< HEAD
description: Routes perturbation datasets to the correct analysis branch (chemical, CRISPR, RNAi, or combinatorial) with route-specific preprocessing parameters. Use after paper assessment to determine analysis pathway, or when a user asks "how should I analyze this perturbation data" or "what preprocessing parameters for a CRISPR/drug screen".
metadata:
    skill-author: K-Dense Inc.
=======
description: Routes assessed papers and datasets to the appropriate analysis branch based on perturbation type (chemical, genetic, etc.). Each perturbation type has different preprocessing parameters, analysis methods, and interpretation frameworks.
>>>>>>> ryan/skill-integration
---

# Perturbation Type Router

## Purpose
Routes assessed papers/datasets to the appropriate analysis branch based on perturbation type. Each perturbation type has different preprocessing parameters, analysis methods, and interpretation frameworks.

## When to Use
Invoke after `concurrent-assessment-workflow` produces a consensus ranking. This router determines which analysis pathway to follow for each dataset.

## Routing Logic

### Input
Takes the top-ranked papers from the consensus ranking, along with the structured query's `perturbation_type` field.

### Route Decision Tree

```
perturbation_type
├── "chemical"
│   └── Chemical Perturbation Path
├── "genetic_crispr"
│   └── Genetic Perturbation Path (CRISPR)
├── "genetic_rnai"
│   └── Genetic Perturbation Path (RNAi)
├── "combinatorial"
│   └── Combinatorial Perturbation Path
└── "unknown"
    └── Infer from data, or ask user
```

### Route A: Chemical Perturbation Path
**Applies to**: Drug treatments, compound screens, dose-response experiments

**Preprocessing parameters**:
```json
{
  "route": "chemical",
  "preprocessing": {
    "min_genes": 200,
    "min_cells": 50,
    "max_pct_mito": 20,
    "n_top_genes": 3000,
    "normalization": "total_count_1e4_log1p",
    "batch_key": "plate",
    "perturbation_key": "compound",
    "dose_key": "dose_um",
    "control_key": "DMSO"
  },
  "analysis_focus": [
    "dose_response_curves",
    "ec50_estimation",
    "moa_clustering",
    "off_target_signatures"
  ],
  "de_method": "wilcoxon",
  "de_groupby": "compound"
}
```

### Route B: Genetic Perturbation Path (CRISPR)
**Applies to**: CRISPR knockout/activation/inhibition screens

**Preprocessing parameters**:
```json
{
  "route": "genetic_crispr",
  "preprocessing": {
    "min_genes": 200,
    "min_cells": 30,
    "max_pct_mito": 25,
    "n_top_genes": 4000,
    "normalization": "total_count_1e4_log1p",
    "batch_key": "replicate",
    "perturbation_key": "gene_target",
    "guide_key": "sgRNA_id",
    "control_key": "non-targeting"
  },
  "analysis_focus": [
    "knockout_efficiency",
    "on_target_vs_off_target",
    "pathway_enrichment",
    "essential_gene_overlap"
  ],
  "de_method": "wilcoxon",
  "de_groupby": "gene_target"
}
```

### Route C: Genetic Perturbation Path (RNAi)
**Applies to**: shRNA/siRNA knockdown experiments

**Preprocessing parameters**:
```json
{
  "route": "genetic_rnai",
  "preprocessing": {
    "min_genes": 200,
    "min_cells": 30,
    "max_pct_mito": 25,
    "n_top_genes": 3000,
    "normalization": "total_count_1e4_log1p",
    "batch_key": "replicate",
    "perturbation_key": "gene_target",
    "construct_key": "shrna_id",
    "control_key": "scramble"
  },
  "analysis_focus": [
    "knockdown_efficiency",
    "seed_effect_assessment",
    "construct_concordance",
    "pathway_enrichment"
  ],
  "de_method": "wilcoxon",
  "de_groupby": "gene_target"
}
```

### Route D: Combinatorial Perturbation Path
**Applies to**: Drug combinations, gene-drug interactions, multi-gene perturbations

**Preprocessing parameters**:
```json
{
  "route": "combinatorial",
  "preprocessing": {
    "min_genes": 200,
    "min_cells": 30,
    "max_pct_mito": 20,
    "n_top_genes": 4000,
    "normalization": "total_count_1e4_log1p",
    "batch_key": "plate",
    "perturbation_key": "combination_id",
    "component_keys": ["perturbation_1", "perturbation_2"],
    "control_key": "vehicle"
  },
  "analysis_focus": [
    "synergy_scoring",
    "interaction_effects",
    "single_vs_combination",
    "epistasis_analysis"
  ],
  "de_method": "wilcoxon",
  "de_groupby": "combination_id"
}
```

### Route E: Unknown Type
If perturbation type is `unknown`:
1. Check if the dataset metadata contains clues (look for column names like `sgRNA`, `compound`, `dose`)
2. If determinable from data, auto-route with a confidence note
3. If still ambiguous, ask the user: "This dataset appears to contain [clues]. Is this a chemical or genetic perturbation experiment?"

## Output Format

```json
{
  "route": "<chemical|genetic_crispr|genetic_rnai|combinatorial>",
  "confidence": "<0.0-1.0>",
  "routing_reason": "<why this route was selected>",
  "preprocessing_params": "<route-specific params object>",
  "analysis_focus": ["<route-specific analyses>"],
  "papers_routed": [
    {
      "paper_id": "<DOI>",
      "dataset_accession": "<GEO ID if available>",
      "route": "<assigned route>"
    }
  ]
}
```

## LanceDB Field Mapping

When data comes from LanceDB (`gene_expression` table), the perturbation metadata maps to preprocessing keys as follows:

| DB Field | Route | Maps to Preprocessing Key |
|----------|-------|--------------------------|
| `chemical_perturbation_uid` | chemical | `perturbation_key` (links to `molecules.sample_uid`) |
| `chemical_perturbation_concentration` | chemical | `dose_key` |
| `genetic_perturbation_gene_index` | genetic_crispr / genetic_rnai | `perturbation_key` (links to `genes.gene_index`) |
| `genetic_perturbation_method` | genetic_crispr / genetic_rnai | Determines CRISPR vs RNAi route (values: CRISPR-cas9, CRISPRi, CRISPRa → crispr; siRNA → rnai) |
| `is_control` | all | `control_key` (True = control condition) |
| `perturbation_search_string` | all | Pre-built search tokens: `SM:<uid> GENE_ID:<idx> METHOD:<method>` |

When both `chemical_perturbation_uid` and `genetic_perturbation_gene_index` are present → route to `combinatorial`.

Use `gene-resolver` (at `src/ych/skills/gene-resolver/`) and `molecule-resolver` (at `src/ych/skills/molecule-resolver/`) for identifier validation during ingestion.

## Dependencies
- Uses: `concurrent-assessment-workflow` (input: ranked papers), `query-understanding-workflow` (perturbation type), `lancedb-query` (perturbation field mapping)
- Used by: `dataset-preprocessing-workflow` (passes preprocessing parameters)
