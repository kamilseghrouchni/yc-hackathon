# Gene Resolver — Schema Reference

## Bionty Gene Entity

| Column | Description |
|---|---|
| `ensembl_gene_id` | Ensembl gene identifier (e.g., ENSG00000141510) |
| `symbol` | Official gene symbol (e.g., TP53) |
| `ncbi_gene_id` | NCBI gene identifier |
| `biotype` | Gene biotype (protein_coding, lncRNA, etc.) |
| `description` | Gene description |
| `synonyms` | Pipe-delimited synonyms |

## Validation Functions

```python
# Validate Ensembl IDs
failed = validate_metadata_against_ontology(ids, OntologyEntity.GENE, organism="human", field="ensembl_gene_id")

# Validate gene symbols
failed = validate_metadata_against_ontology(symbols, OntologyEntity.GENE, organism="human", field="symbol")

# Standardize gene symbols (resolve synonyms to canonical)
standardized = standardize_metadata_to_ontology(symbols, OntologyEntity.GENE, organism="human", field="symbol")
```

## Ensembl Prefix to Organism

| Prefix | Organism |
|---|---|
| `ENSG` | human |
| `ENSMUSG` | mouse |
| `ENSRNOG` | rat |
| `ENSDARG` | zebrafish |
| `ENSGALG` | chicken |
| `ENSSSOG` | pig |

## Output Columns

**Var (expression features):**

| Column | Type | Description |
|---|---|---|
| `validated_organism` | `str\|None` | Organism per gene, derived from Ensembl prefix |
| `validated_ensembl_gene_id` | `str\|None` | Validated Ensembl gene ID (version stripped) |
| `validated_gene_symbol` | `str\|None` | Canonical gene symbol |

**Obs (genetic perturbation targets):**

| Column | Type | Description |
|---|---|---|
| `validated_genetic_perturbation` | `str\|None` | Canonical gene symbol (None for controls); combinatorial: `_1`, `_2`, etc. |
| `validated_is_control` | `bool` | True ONLY when explicitly labeled as control; NaN perturbation = False |
| `validated_perturbation_method` | `str` | One of: CRISPR-cas9, CRISPRi, CRISPRa, siRNA, ORF |
