# Query Understanding Workflow

## Purpose
Parses a user's natural language perturbation biology question into a structured query object. This is the entry point of the pipeline — every user question flows through here first.

## When to Use
Invoke this workflow whenever a user asks a perturbation biology question. The output feeds into `paper-search-workflow` and `perturbation-type-router`.

## Workflow Steps

### Step 1: Entity Extraction
Identify biological entities from the user's question:

- **Genes**: gene symbols (e.g., TP53, BRCA1, KRAS), gene families, pathways
- **Drugs/Compounds**: drug names, compound IDs (e.g., nutlin-3a, dexamethasone), MOA classes
- **Cell Types**: cell lines (e.g., A549, K562, MCF7), primary cell types, tissues
- **Diseases**: disease names, disease codes, phenotypes
- **Organisms**: species (default: human if unspecified)
- **Perturbation Agents**: sgRNA targets, shRNA constructs, CRISPR libraries

### Step 2: Question Type Classification
Classify the question into one of these categories:

| Type | Description | Example |
|------|-------------|---------|
| `mechanism` | How does a perturbation work? | "How does KRAS knockout affect downstream signaling?" |
| `comparison` | Compare perturbations or conditions | "Compare dexamethasone vs prednisolone in A549 cells" |
| `dose-response` | Dose or time-dependent effects | "What happens to TP53 targets at different nutlin-3a doses?" |
| `screening` | Large-scale perturbation screen results | "What are the top hits from a genome-wide CRISPR screen in K562?" |
| `dataset-search` | Find relevant datasets | "Find CRISPR screens in lung cancer cell lines" |
| `analysis` | Analyze a specific dataset | "Run differential expression on this perturbation dataset" |

### Step 3: Perturbation Type Detection
Classify the perturbation type:

| Type | Indicators |
|------|-----------|
| `chemical` | Drug names, compound IDs, dose mentions, MOA references |
| `genetic_crispr` | CRISPR, Cas9, sgRNA, guide RNA, knockout, KO |
| `genetic_rnai` | RNAi, shRNA, siRNA, knockdown, KD |
| `combinatorial` | Multiple perturbations, combinations, synergy, interaction |
| `unknown` | Insufficient information to classify |

### Step 4: Build Structured Query Object

Output this JSON structure:

```json
{
  "raw_query": "<original user question>",
  "entities": {
    "genes": ["<gene symbols>"],
    "drugs": ["<drug/compound names>"],
    "cell_types": ["<cell lines or types>"],
    "diseases": ["<disease names>"],
    "organisms": ["<species, default 'Homo sapiens'>"],
    "perturbation_agents": ["<specific constructs if mentioned>"]
  },
  "question_type": "<mechanism|comparison|dose-response|screening|dataset-search|analysis>",
  "perturbation_type": "<chemical|genetic_crispr|genetic_rnai|combinatorial|unknown>",
  "search_terms": ["<derived search keywords for paper/dataset retrieval>"],
  "filters": {
    "organism": "<species filter>",
    "data_availability": "<true if user wants downloadable data>",
    "year_range": [null, null]
  },
  "confidence": {
    "entity_extraction": "<0.0-1.0>",
    "question_classification": "<0.0-1.0>",
    "perturbation_classification": "<0.0-1.0>"
  }
}
```

### Step 5: Ambiguity Resolution
If confidence in any field is below 0.6:
- Ask the user a **single clarifying question** targeting the lowest-confidence field
- Do not ask more than one clarification per query
- If all confidences are above 0.6, proceed directly

## Output
Return the structured query JSON to the calling workflow (typically the main orchestrator or `paper-search-workflow`).

## Examples

**Input**: "What are the effects of KRAS knockout in A549 cells?"
```json
{
  "raw_query": "What are the effects of KRAS knockout in A549 cells?",
  "entities": {
    "genes": ["KRAS"],
    "drugs": [],
    "cell_types": ["A549"],
    "diseases": [],
    "organisms": ["Homo sapiens"],
    "perturbation_agents": []
  },
  "question_type": "mechanism",
  "perturbation_type": "genetic_crispr",
  "search_terms": ["KRAS", "knockout", "A549", "CRISPR"],
  "filters": {
    "organism": "Homo sapiens",
    "data_availability": true,
    "year_range": [null, null]
  },
  "confidence": {
    "entity_extraction": 0.95,
    "question_classification": 0.85,
    "perturbation_classification": 0.90
  }
}
```

### Step 6: Resolve Identifiers Against DB (Optional)

When LanceDB is available, validate extracted entities against the curated registries:

- **Genes**: Look up gene names in the `genes` table (`GeneSchema`) to get `gene_index` and canonical `ensembl_id`. Uses `gene-resolver` skill for standardization (Bionty ontologies, Ensembl prefix → organism mapping).
- **Drugs/Compounds**: Look up compound names in the `molecules` table (`MoleculeSchema`) to get `pubchem_cid` and `sample_uid`. Uses `molecule-resolver` skill for PubChem resolution.
- **Enriched query output**: If resolved, add `resolved_gene_indices` and `resolved_molecule_uids` to the structured query for direct DB filtering downstream.

This step is optional — the pipeline works without it (falls back to text-based search). But when available, it enables precise perturbation-level queries against `gene_expression` table's `perturbation_search_string` field.

## Dependencies
- Used by: Main orchestrator, `paper-search-workflow`, `perturbation-type-router`
- Uses: `lancedb-query` (optional, for identifier resolution), `gene-resolver`, `molecule-resolver` (via `src/ych/skills/`)
