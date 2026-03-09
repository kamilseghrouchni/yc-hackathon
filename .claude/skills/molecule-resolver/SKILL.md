---
name: molecule-resolver
description: Resolve chemical compound names or SMILES to PubChem CIDs in standardized_obs.csv files. Handles name cleanup, fallback resolution, control label filtering, and is_control derivation for chemical perturbation datasets.
---

# Molecule Resolver

Resolve chemical compound identifiers in `standardized_obs.csv` and add `validated_*` columns with PubChem CIDs and control status for downstream ingestion.

## Interface

- **Input:** `{key}_standardized_obs.csv` with a chemical perturbation precursor column
- **Output:** Same CSV with `validated_chemical_perturbation`, `validated_chemical_perturbation_pubchem_cid`, and `validated_is_control` columns added
- **Rule:** Save the CSV after adding each column to prevent losing work

## Imports

```python
from ych.ingestion_utils import resolve_pubchem_cids
```

## Workflow

### 1. Load the obs CSV and extract unique compounds

```python
import pandas as pd
from pathlib import Path

data_dir = Path("/tmp/geo_agent/<accession>")
obs_csv_path = data_dir / f"{key}_standardized_obs.csv"
standardized_obs = pd.read_csv(obs_csv_path, index_col=0)

# Identify the precursor compound column
compound_col = "<compound_column>"  # e.g., "compound", "drug", "treatment"
compound_names = standardized_obs[compound_col].dropna().unique().tolist() if compound_col in standardized_obs.columns else obs_df[compound_col].dropna().unique().tolist()
print(f"Unique compounds: {len(compound_names)}")
print(compound_names[:20])
```

### 2. Detect control labels

Control labels should not be resolved — they map to `None` in the compound columns and `True` in `is_control`:

```python
CONTROL_LABELS = {"dmso", "vehicle", "control", "untreated", "mock", "pbs", "media", "medium", "none", "empty"}

actual_compounds = [c for c in compound_names if c.strip().lower() not in CONTROL_LABELS]
control_compounds = [c for c in compound_names if c.strip().lower() in CONTROL_LABELS]
print(f"Actual compounds: {len(actual_compounds)}, Controls: {len(control_compounds)}")
```

### 3. Derive `validated_is_control`

```python
def derive_is_control(value) -> bool:
    if pd.isna(value):
        # NaN compound does NOT imply control — cell may have been treated
        # but compound identity is unknown
        return False
    if str(value).strip().lower() in CONTROL_LABELS:
        return True
    return False

source_col = compound_col if compound_col in standardized_obs.columns else None
source_series = standardized_obs[compound_col] if source_col else obs_df[compound_col]
standardized_obs["validated_is_control"] = source_series.apply(derive_is_control)
standardized_obs.to_csv(obs_csv_path)
```

**Critical rule:** `is_control=True` ONLY when the dataset explicitly labels a cell as a control (DMSO, vehicle, etc.). Cells with NaN/None compound (e.g., unassigned wells) should have `is_control=False`.

### 4. Initial resolution

```python
resolved, unresolved = resolve_pubchem_cids(names=actual_compounds)
# resolved: dict[str, int] — name -> PubChem CID
# unresolved: set[str] — names that didn't resolve
print(f"Resolved: {len(resolved)}, Unresolved: {len(unresolved)}")
```

### 5. Save initial results

Save immediately so partial progress is preserved:

```python
name_map = {c: c for c in actual_compounds}
for c in control_compounds:
    name_map[c] = None

standardized_obs["validated_chemical_perturbation"] = source_series.map(name_map)
standardized_obs["validated_chemical_perturbation_pubchem_cid"] = source_series.map(resolved)
standardized_obs.to_csv(obs_csv_path)
```

### 6. Fix unresolved compound names

For compound **names** that didn't resolve (not SMILES — SMILES failures are acceptable), inspect each one. Common issues:

- **Stray characters:** `Glesatinib?(MGCD265)` -> `Glesatinib`
- **Parenthetical aliases:** `Abexinostat (PCI-24781)` -> `Abexinostat`
- **Salt forms appended:** `Obatoclax Mesylate (GX15-070)` -> `Obatoclax Mesylate`
- **Trailing whitespace:** `Busulfan ` -> `Busulfan`
- **Underscore-joined identifiers:** `Drug_123` -> `Drug`

Build a correction mapping:

```python
corrections = {
    "Glesatinib?(MGCD265)": "Glesatinib",
    "Tucidinostat (Chidamide)": "Tucidinostat",
    # ... agent builds this by inspecting unresolved names
}

standardized_obs["validated_chemical_perturbation"] = (
    standardized_obs["validated_chemical_perturbation"].replace(corrections)
)
```

### 7. Re-resolve corrected names

```python
corrected_names = list(corrections.values())
resolved_corrections, still_unresolved = resolve_pubchem_cids(names=corrected_names)

all_resolved = {**resolved, **resolved_corrections}
for orig, fixed in corrections.items():
    if fixed in resolved_corrections:
        all_resolved[orig] = resolved_corrections[fixed]

standardized_obs["validated_chemical_perturbation_pubchem_cid"] = (
    standardized_obs["validated_chemical_perturbation"].map(all_resolved)
)
standardized_obs.to_csv(obs_csv_path)

if still_unresolved:
    print(f"Still unresolved after correction: {still_unresolved}")
    print("Flag these for user review.")
```

### 8. SMILES fallback (if applicable)

If the dataset provides SMILES strings and some names failed:

```python
smiles_for_unresolved = [smiles_map[name] for name in still_unresolved if name in smiles_map]
if smiles_for_unresolved:
    resolved_smiles, _ = resolve_pubchem_cids(smiles=smiles_for_unresolved)
    # Merge results
```

SMILES that don't resolve may simply not be in PubChem — leave them as-is with CID=None.

## Resolution Strategy

All `validated_*` columns follow the same principle: **never NaN unless there is genuinely no value.**

- **`validated_chemical_perturbation`** (compound name): Always populated for non-control cells. Use the cleaned/corrected name when resolution succeeds, the original name as-is when it doesn't. Only NaN for control cells (where the value becomes None) or cells with genuinely no compound identity.
- **`validated_chemical_perturbation_pubchem_cid`** (integer CID): NaN is acceptable here when PubChem doesn't have the compound — you can't put a compound name in a CID field. But the name column should still have the value.
- **`validated_is_control`**: True for explicit controls, False otherwise. NaN compound does NOT imply control.

This ensures the downstream curator can distinguish "compound known but not in PubChem" (name present, CID NaN) from "no compound data at all" (both NaN).

## Rules

- **`is_control=True` ONLY for explicit controls.** DMSO, vehicle, control, untreated, etc. NaN/None compound does NOT imply control.
- **Control labels map to None** in `validated_chemical_perturbation` and `validated_chemical_perturbation_pubchem_cid`.
- **Name failures must be investigated.** Do not silently leave compound names unresolved. Inspect each failure, apply corrections, and re-resolve.
- **SMILES failures are acceptable.** Not all SMILES are in PubChem. Leave with CID=NaN but keep the compound name in `validated_chemical_perturbation`.
- **Never set `validated_chemical_perturbation` to NaN for non-control cells.** If a name can't be resolved to a CID, keep the name — only the CID column should be NaN.
- **Save after each column** to prevent losing work on interruption.
- **Never modify h5ad files.** All validated data goes into the CSV only.
- **Flag remaining unresolved names** for user review. Do not silently drop them.
