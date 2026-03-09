---
name: gene-resolver
description: Validate and standardize gene identifiers in standardized_var.csv (Ensembl IDs, symbols, barnyard detection) and genetic perturbation targets in standardized_obs.csv (control detection, combinatorial splitting, is_control derivation, perturbation method).
---

# Gene Resolver

Validate gene identifiers wherever they appear: in `standardized_var.csv` (expression features) and in `standardized_obs.csv` (genetic perturbation targets). Both are gene symbols validated against the same ontology.

## Interface

**var (expression features):**
- **Input:** `{key}_standardized_var.csv` with gene identifiers in the index (Ensembl IDs or gene symbols)
- **Output:** Same CSV with `validated_organism`, `validated_ensembl_gene_id`, and `validated_gene_symbol` columns added

**obs (genetic perturbation targets):**
- **Input:** `{key}_standardized_obs.csv` with a genetic perturbation precursor column
- **Output:** Same CSV with `validated_genetic_perturbation`, `validated_is_control`, and `validated_perturbation_method` columns added (plus combinatorial columns `_1`, `_2`, etc. if needed)

**Rule:** Save the CSV after adding each column to prevent losing work.

## Imports

```python
from ych.ingestion_utils import (
    OntologyEntity,
    validate_metadata_against_ontology,
    standardize_metadata_to_ontology,
    search_metadata_in_ontology,
)
```

---

## Part A: Var Gene Validation

### A1. Load the var CSV

```python
import pandas as pd
from pathlib import Path

data_dir = Path("/tmp/geo_agent/<accession>")
var_csv_path = data_dir / f"{key}_standardized_var.csv"
standardized_var = pd.read_csv(var_csv_path, index_col=0)
```

Also load the original var dataframe (from the h5ad or matrix companions) to access gene identifiers and symbols that may be in separate columns.

### A2. Detect identifier format

Determine whether the var index contains Ensembl IDs or gene symbols:

```python
var_index_sample = standardized_var.index[:10].tolist()
is_ensembl = any(str(v).startswith("ENS") for v in var_index_sample)
```

If the index is Ensembl IDs, gene symbols may be in a separate column (e.g., `gene_symbols`, `gene_name`, `feature_name`). If the index is gene symbols, Ensembl IDs may be in a column like `gene_ids`.

### A3. Detect organisms from Ensembl prefixes (barnyard detection)

```python
ENSEMBL_PREFIX_TO_ORGANISM = {
    "ENSG": "human",
    "ENSMUSG": "mouse",
    "ENSRNOG": "rat",
    "ENSDARG": "zebrafish",
    "ENSGALG": "chicken",
    "ENSSSOG": "pig",
}

def detect_organism_from_ensembl_id(eid: str) -> str | None:
    """Map an Ensembl gene ID to its organism based on prefix."""
    for prefix, organism in ENSEMBL_PREFIX_TO_ORGANISM.items():
        if eid.startswith(prefix):
            return organism
    return None

# Get Ensembl IDs (from index or column)
ensembl_ids = [str(eid).split(".")[0] for eid in ensembl_id_source]

gene_organisms = [detect_organism_from_ensembl_id(eid) for eid in ensembl_ids]
unique_organisms = set(o for o in gene_organisms if o is not None)

print(f"Organisms detected: {unique_organisms}")
for org in unique_organisms:
    count = sum(1 for o in gene_organisms if o == org)
    print(f"  {org}: {count} genes")

unrecognized = sum(1 for o in gene_organisms if o is None)
if unrecognized:
    print(f"  unrecognized prefix: {unrecognized} genes")
```

If multiple organisms are detected, this is a **barnyard experiment**. Report the finding to the user and proceed with per-organism validation.

### A4. Write `validated_organism`

**Always** write this column, even for single-organism datasets. It tells the downstream curator which organism to register each gene under.

```python
standardized_var["validated_organism"] = gene_organisms
standardized_var.to_csv(var_csv_path)
```

### A5. Validate Ensembl IDs (per organism)

Strip version suffixes (e.g., `ENSG00000141510.16` -> `ENSG00000141510`) before validation.

```python
for organism in unique_organisms:
    org_mask = [o == organism for o in gene_organisms]
    org_ensembl_ids = [eid for eid, m in zip(ensembl_ids, org_mask) if m]

    failed = validate_metadata_against_ontology(
        org_ensembl_ids, OntologyEntity.GENE, organism=organism, field="ensembl_gene_id"
    )
    print(f"{organism}: {len(org_ensembl_ids)} genes, {len(failed)} failed validation")
    if failed:
        print(f"  Sample failures: {failed[:10]}")
```

Some failures are expected (RNAs, pseudogenes, deprecated IDs). Log them but do not treat as blocking errors.

**Old Ensembl versions:** If a large fraction of IDs fail (suggesting GRCh37/hg19 vs GRCh38/hg38 mismatch), attempt to recover by getting current Ensembl IDs from gene symbols:

```python
failed_symbols = [sym for eid, sym, m in zip(ensembl_ids, gene_symbols, org_mask) if m and eid in failed_set]
standardized_symbols = standardize_metadata_to_ontology(
    failed_symbols, OntologyEntity.GENE, organism=organism, field="symbol"
)
```

**Resolution strategy for `validated_ensembl_gene_id`:** Use the standardized Ensembl ID when validation succeeds. When it fails, **use the original stripped Ensembl ID as-is** — do not set to NaN. The ID is still a real identifier in the matrix even if the ontology doesn't recognize it (deprecated, non-coding, etc.). The downstream curator needs a mapping for every positional gene.

```python
# standardize_metadata_to_ontology already returns the original value for unmatched IDs
standardized = standardize_metadata_to_ontology(
    org_ensembl_ids, OntologyEntity.GENE, organism=organism, field="ensembl_gene_id"
)
# This gives us: matched IDs -> canonical ID, unmatched IDs -> original ID (never NaN)
```

Save validated Ensembl IDs:

```python
standardized_var["validated_ensembl_gene_id"] = all_validated_ensembl_ids
standardized_var.to_csv(var_csv_path)
```

### A6. Validate and standardize gene symbols (per organism)

```python
for organism in unique_organisms:
    org_mask = [o == organism for o in gene_organisms]
    org_symbols = [sym for sym, m in zip(gene_symbols, org_mask) if m]

    failed = validate_metadata_against_ontology(
        org_symbols, OntologyEntity.GENE, organism=organism, field="symbol"
    )

    standardized = standardize_metadata_to_ontology(
        org_symbols, OntologyEntity.GENE, organism=organism, field="symbol"
    )
    std_iter = iter(standardized)
    for i, m in enumerate(org_mask):
        if m:
            all_standardized_symbols[i] = next(std_iter)
```

**Resolution strategy for `validated_gene_symbol`:** `standardize_metadata_to_ontology` returns the original symbol when it can't match — this is the correct behavior. lncRNAs like `AC000061.1` or Riken clones like `1700049J03Rik` keep their original names. Do not replace with NaN.

Save validated gene symbols:

```python
standardized_var["validated_gene_symbol"] = all_standardized_symbols
standardized_var.to_csv(var_csv_path)
```

RNA/pseudogene failures are acceptable. Report the count and a sample but do not block.

---

## Part B: Genetic Perturbation Target Validation (obs)

Skip this part if the dataset has no genetic perturbation columns.

### B1. Load the obs CSV and identify the perturbation column

```python
obs_csv_path = data_dir / f"{key}_standardized_obs.csv"
standardized_obs = pd.read_csv(obs_csv_path, index_col=0)

target_col = "<target_column>"  # e.g., "gene", "target_gene", "sgRNA_target", "perturbation"
unique_targets = obs_df[target_col].dropna().unique().tolist()
print(f"Unique perturbation targets ({len(unique_targets)}):")
print(unique_targets[:20])
```

### B2. Detect control labels

```python
CONTROL_PATTERNS = {
    "non-targeting", "nontargeting", "non_targeting",
    "safe-targeting", "safe_targeting",
    "control", "ctrl",
    "egfp", "gfp", "luciferase", "lacz",
    "scramble", "scrambled",
    "empty", "mock",
}

def is_control_label(value: str) -> bool:
    """Check if a value is a known control label."""
    v = value.strip().lower()
    if v in CONTROL_PATTERNS:
        return True
    # Prefix match for numbered controls: NegCtrl0, NegCtrl1, NegCtrl10, etc.
    if v.startswith("negctrl") or v.startswith("neg_ctrl") or v.startswith("neg-ctrl"):
        return True
    return False

control_labels = [t for t in unique_targets if is_control_label(t)]
actual_targets = [t for t in unique_targets if not is_control_label(t)]
print(f"Control labels: {control_labels}")
print(f"Actual targets: {len(actual_targets)}")
```

**Important:** Inspect unique values carefully. Datasets often have multiple control label variants (e.g., `NegCtrl0`, `NegCtrl1`, `NegCtrl10`, `non-targeting`, `safe-targeting`). Catch them all.

### B3. Detect and split combinatorial perturbations

Some datasets encode multiple targets in one column (e.g., `"AHR_KLF1"`, `"TP53+BRCA1"`):

```python
delimiters = ["_", "+", "|", ";"]
max_parts = 1
chosen_delimiter = None

for target in actual_targets:
    for delim in delimiters:
        parts = target.split(delim)
        if len(parts) > max_parts:
            max_parts = len(parts)
            chosen_delimiter = delim

if max_parts > 1:
    print(f"Combinatorial perturbations detected (delimiter: '{chosen_delimiter}', max targets: {max_parts})")
```

**Caution with underscores:** Gene names can contain underscores (e.g., `C1orf43`). Only treat underscore as a delimiter if splitting consistently produces valid gene names. Test a sample before committing.

### B4. Validate gene targets

For single-target datasets:

```python
failed = validate_metadata_against_ontology(
    actual_targets, OntologyEntity.GENE, organism="human", field="symbol"
)
if failed:
    print(f"{len(failed)} gene targets failed validation: {failed[:10]}")
    for gene in failed:
        results = search_metadata_in_ontology(gene, OntologyEntity.GENE)
        print(f"  '{gene}' -> closest matches:")
        print(results.head(3))

standardized = standardize_metadata_to_ontology(
    actual_targets, OntologyEntity.GENE, organism="human", field="symbol"
)
target_map = dict(zip(actual_targets, standardized))
```

For combinatorial datasets, split and validate each part independently:

```python
all_individual_targets = set()
for target in actual_targets:
    for part in target.split(chosen_delimiter):
        part = part.strip()
        if part and not is_control_label(part):
            all_individual_targets.add(part)

all_individual_targets = list(all_individual_targets)
failed = validate_metadata_against_ontology(
    all_individual_targets, OntologyEntity.GENE, organism="human", field="symbol"
)
standardized = standardize_metadata_to_ontology(
    all_individual_targets, OntologyEntity.GENE, organism="human", field="symbol"
)
individual_map = dict(zip(all_individual_targets, standardized))
```

### B5. Derive `validated_is_control`

```python
def derive_is_control(value) -> bool | None:
    if pd.isna(value):
        # NaN perturbation does NOT imply control
        return False
    if is_control_label(str(value)):
        return True
    return False

standardized_obs["validated_is_control"] = obs_df[target_col].apply(derive_is_control)
standardized_obs.to_csv(obs_csv_path)
```

**Critical rule:** `is_control=True` ONLY when the dataset explicitly labels a cell as a control. Cells with NaN/None perturbation (e.g., no detected guide barcode) should have `is_control=False`.

### B6. Write validated perturbation columns

For single-target datasets:

```python
for label in control_labels:
    target_map[label] = None

standardized_obs["validated_genetic_perturbation"] = obs_df[target_col].map(target_map)
standardized_obs.to_csv(obs_csv_path)
```

For combinatorial datasets:

```python
for i in range(max_parts):
    col_name = f"validated_genetic_perturbation_{i + 1}"
    def get_part(value, idx=i):
        if pd.isna(value) or is_control_label(str(value)):
            return None
        parts = str(value).split(chosen_delimiter)
        if idx < len(parts):
            part = parts[idx].strip()
            if is_control_label(part):
                return None
            return individual_map.get(part, part)
        return None
    standardized_obs[col_name] = obs_df[target_col].apply(get_part)
    standardized_obs.to_csv(obs_csv_path)
```

### B7. Validate perturbation method

```python
VALID_METHODS = {"CRISPR-cas9", "CRISPRi", "CRISPRa", "siRNA", "ORF"}

method = "<method>"  # from GEO metadata or obs column
if method not in VALID_METHODS:
    print(f"WARNING: '{method}' not in valid methods: {VALID_METHODS}")

standardized_obs["validated_perturbation_method"] = method
standardized_obs.to_csv(obs_csv_path)
```

---

## Resolution Strategy

All `validated_*` columns follow the same principle: **never NaN unless there is genuinely no value.**

1. **Standardization succeeds** → use the canonical value (e.g., synonym resolved to official symbol).
2. **Standardization fails** → use the original value as-is. `standardize_metadata_to_ontology()` already returns the original value for unmatched inputs — do not replace with NaN. A deprecated Ensembl ID or an unrecognized gene symbol is still valid data.
3. **NaN only when no value exists** — e.g., a gene has no symbol at all, or a cell has no perturbation target.
4. **Control labels → None** — "non-targeting", "NegCtrl0", etc. become None in perturbation columns (they inform `is_control`, not the gene field).

This ensures the downstream curator can trust that NaN means "no data" rather than "validation failed," and can build positional mappings for every gene without gaps.

## Rules

**Var (Part A):**
- **Strip version suffixes** from Ensembl IDs before validation (split on `.`).
- **Always write `validated_organism`** to standardized_var, even for single-organism datasets.
- **RNA/pseudogene failures are acceptable.** Report the count and sample failures but do not block.
- **Validate per organism** when multiple organisms are detected (barnyard experiments).
- **Old Ensembl versions:** If a large fraction of Ensembl IDs fail, attempt recovery via gene symbols before giving up.
- **Never set validated columns to NaN for failed validation.** Use the original value. See Resolution Strategy above.

**Obs (Part B):**
- **`is_control=True` ONLY for explicit controls.** NaN/None perturbation does NOT imply control.
- **Control labels map to None in perturbation columns.** "non-targeting", "NegCtrl*", "eGFP", "scramble", etc. inform `is_control`, not the gene target.
- **Watch for multiple control label variants.** Datasets often have NegCtrl0, NegCtrl1, NegCtrl10, safe-targeting, etc.
- **Validate each combinatorial column independently.** Split targets into separate columns and validate each part as its own gene symbol.
- **Validate perturbation method** against the constrained set: CRISPR-cas9, CRISPRi, CRISPRa, siRNA, ORF.

**Both:**
- **Save after each column** to prevent losing work on interruption.
- **Never modify h5ad files.** All validated data goes into the CSV only.
- **Ask before guessing.** If the delimiter or control labels are ambiguous, ask the user.
