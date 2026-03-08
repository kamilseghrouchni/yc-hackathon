# Molecule Resolver — Schema Reference

## PubChem Resolution

The `resolve_pubchem_cids` function resolves compound names or SMILES to PubChem Compound IDs (CIDs).

```python
from ych.ingestion_utils import resolve_pubchem_cids

# Resolve by name
resolved, unresolved = resolve_pubchem_cids(names=["Imatinib", "Dexamethasone"])
# resolved: {"Imatinib": 5291, "Dexamethasone": 5743}
# unresolved: set()

# Resolve by SMILES
resolved, unresolved = resolve_pubchem_cids(smiles=["CC(=O)Oc1ccccc1C(=O)O"])
```

## Output Columns

| Column | Type | Description |
|---|---|---|
| `validated_chemical_perturbation` | `str\|None` | Cleaned compound name (None for controls) |
| `validated_chemical_perturbation_pubchem_cid` | `int\|None` | PubChem CID (None for controls or unresolved) |
| `validated_is_control` | `bool` | True for DMSO/vehicle/control labels; NaN compound = False |

## Control Labels (map to None in compound columns, True in is_control)

DMSO, vehicle, control, untreated, mock, PBS, media, medium, none, empty
