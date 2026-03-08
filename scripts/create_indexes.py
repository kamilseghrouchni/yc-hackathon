"""Create scalar and FTS indexes for all tables.

Usage:
    python scripts/create_indexes.py [db_uri]
"""

import argparse

import lancedb

_DEFAULT_URI = "s3://ychackathon-cell-data/yclance/"

TABLE_INDEXES: dict[str, list[tuple[str, str, dict]]] = {
    "publications": [
        ("pmid", "scalar", {}),
        ("doi", "scalar", {}),
        ("journal", "scalar", {}),
        ("section_title", "scalar", {}),
        ("section_text", "fts", {}),
    ],
    "datasets": [
        ("pmid", "scalar", {}),
        ("doi", "scalar", {}),
        ("feature_space", "scalar", {}),
        ("accession_database", "scalar", {}),
        ("accession_id", "scalar", {}),
        ("dataset_uid", "scalar", {}),
        ("dataset_description", "fts", {}),
    ],
    "genes": [
        ("gene_index", "scalar", {}),
        ("gene_name", "scalar", {}),
        ("ensembl_id", "scalar", {}),
        ("organism", "scalar", {}),
    ],
    "image_features": [
        ("feature_index", "scalar", {}),
        ("feature_name", "scalar", {}),
    ],
    "gene_expression": [
        ("cell_uid", "scalar", {}),
        ("dataset_uid", "scalar", {}),
        ("assay", "scalar", {}),
        ("is_control", "scalar", {"index_type": "BITMAP"}),
        (
            "perturbation_search_string",
            "fts",
            {"base_tokenizer": "whitespace", "stem": False, "lower_case": False},
        ),
    ],
    "image_feature_vectors": [
        ("cell_uid", "scalar", {}),
        ("dataset_uid", "scalar", {}),
        ("assay", "scalar", {}),
        ("is_control", "scalar", {"index_type": "BITMAP"}),
        (
            "perturbation_search_string",
            "fts",
            {"base_tokenizer": "whitespace", "stem": False, "lower_case": False},
        ),
    ],
}


def create_indexes(db_uri: str) -> None:
    db = lancedb.connect(db_uri)
    existing_tables = {t for t in db.list_tables().tables}

    for table_name, indexes in TABLE_INDEXES.items():
        if table_name not in existing_tables:
            print(f"SKIP  {table_name!r} — not found in {db_uri}")
            continue

        table = db.open_table(table_name)
        print(f"\n--- {table_name} ({table.count_rows()} rows) ---")

        table.optimize()

        for column, index_type, kwargs in indexes:
            try:
                if index_type == "scalar":
                    table.create_scalar_index(column, replace=True, **kwargs)
                    print(f"  scalar index: {column}")
                elif index_type == "fts":
                    table.create_fts_index(column, replace=True, **kwargs)
                    print(f"  fts    index: {column}")
            except Exception as exc:
                print(f"  FAILED {index_type} index on {column}: {exc}")

        table.optimize()

    print("\nDone")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Create indexes for all tables")
    parser.add_argument("db_uri", nargs="?", default=_DEFAULT_URI, help="LanceDB URI")
    args = parser.parse_args()
    create_indexes(args.db_uri)
