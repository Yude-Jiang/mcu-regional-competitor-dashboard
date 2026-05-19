#!/usr/bin/env python3
"""setup_gcp.py — One-time setup: create BigQuery dataset/tables + GCS bucket.

Usage:
    python setup_gcp.py                         # uses GCP_PROJECT env var
    python setup_gcp.py --project my-project    # explicit project
    python setup_gcp.py --dry-run               # show what would be created
"""

import argparse
import os
import sys
from pathlib import Path

try:
    from google.cloud import bigquery, storage
    from google.api_core.exceptions import Conflict, NotFound
except ImportError:
    sys.exit("Missing deps: pip install google-cloud-bigquery google-cloud-storage")

HERE = Path(__file__).parent

BQ_DATASET    = "mcu"
BQ_LOCATION   = "asia-east2"   # Hong Kong — accessible from mainland China
GCS_BUCKET    = "mcu-annual-reports"
GCS_LOCATION  = "asia-east2"

TABLES = [
    "financials",
    "pdf_index",
    "mcu_segments",
    "qa_cache",
]


def get_project(args) -> str:
    p = args.project or os.environ.get("GCP_PROJECT") or os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not p:
        sys.exit(
            "GCP project not set. Use --project or set GCP_PROJECT env var.\n"
            "  export GCP_PROJECT=your-project-id"
        )
    return p


def create_dataset(bq: bigquery.Client, project: str, dry_run: bool) -> None:
    dataset_id = f"{project}.{BQ_DATASET}"
    print(f"BigQuery dataset: {dataset_id}  (location={BQ_LOCATION})")
    if dry_run:
        return
    dataset = bigquery.Dataset(dataset_id)
    dataset.location = BQ_LOCATION
    dataset.description = "MCU Regional Competitor Dashboard — financial data"
    try:
        bq.create_dataset(dataset, timeout=30)
        print(f"  ✓ Created dataset {dataset_id}")
    except Conflict:
        print(f"  ● Dataset already exists — skipped")


def create_tables(bq: bigquery.Client, project: str, dry_run: bool) -> None:
    schema_sql = (HERE / "bigquery_schema.sql").read_text()
    # Replace {project} placeholder
    schema_sql = schema_sql.replace("{project}", project)

    for table_name in TABLES:
        table_id = f"{project}.{BQ_DATASET}.{table_name}"
        print(f"BigQuery table: {table_id}")
        if dry_run:
            continue
        # Extract the CREATE TABLE statement for this table
        marker = f"CREATE TABLE IF NOT EXISTS `{project}.{BQ_DATASET}.{table_name}`"
        if marker not in schema_sql:
            print(f"  ✗ Schema not found for {table_name} — skipped")
            continue
        start = schema_sql.index(marker)
        # Find the closing semicolon after this statement
        end = schema_sql.index(";", start) + 1
        stmt = schema_sql[start:end]
        try:
            bq.query(stmt).result()
            print(f"  ✓ Table created (or already exists)")
        except Exception as exc:
            print(f"  ✗ Failed: {exc}")


def create_gcs_bucket(gcs: storage.Client, project: str, dry_run: bool) -> None:
    print(f"GCS bucket: gs://{GCS_BUCKET}  (location={GCS_LOCATION})")
    if dry_run:
        return
    try:
        bucket = gcs.create_bucket(
            GCS_BUCKET,
            project=project,
            location=GCS_LOCATION,
        )
        # Enable versioning so accidental deletes are recoverable
        bucket.versioning_enabled = True
        bucket.patch()
        print(f"  ✓ Bucket created with versioning enabled")
    except Conflict:
        print(f"  ● Bucket already exists — skipped")
    except Exception as exc:
        print(f"  ✗ Failed: {exc}")

    # Create placeholder objects to establish folder structure
    folders = [
        "reports/",           # PDF originals: reports/{symbol}/{year}_{type}.pdf
        "extracted/",         # LLM outputs:   extracted/{symbol}/{year}_{type}.json
    ]
    if not dry_run:
        bucket = gcs.bucket(GCS_BUCKET)
        for folder in folders:
            blob = bucket.blob(folder + ".keep")
            if not blob.exists():
                blob.upload_from_string("", content_type="text/plain")
                print(f"  ✓ Created folder placeholder: gs://{GCS_BUCKET}/{folder}")


def print_next_steps(project: str) -> None:
    print(f"""
{'='*60}
Setup complete. Next steps:
{'='*60}

1. Verify BigQuery dataset:
   bq ls --project={project} {BQ_DATASET}

2. Verify GCS bucket:
   gsutil ls gs://{GCS_BUCKET}/

3. Run AKShare sync to populate BigQuery:
   python smart_sync.py

4. Set Secret Manager secrets (if not already done):
   gcloud secrets create deepseek-api-key --project={project}
   echo -n "sk-..." | gcloud secrets versions add deepseek-api-key --data-file=-

   gcloud secrets create gemini-api-key --project={project}
   echo -n "AIza..." | gcloud secrets versions add gemini-api-key --data-file=-

5. GCS bucket path for PDFs:
   gs://{GCS_BUCKET}/reports/{{symbol}}/{{year}}_{{type}}.pdf
   e.g. gs://{GCS_BUCKET}/reports/603986/2024_年报.pdf
""")


def main() -> None:
    parser = argparse.ArgumentParser(description="Setup GCP resources for MCU dashboard")
    parser.add_argument("--project", help="GCP project ID")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be created")
    args = parser.parse_args()

    project = get_project(args)
    print(f"\nProject: {project}  {'[DRY RUN]' if args.dry_run else ''}\n")

    bq  = bigquery.Client(project=project)
    gcs = storage.Client(project=project)

    create_dataset(bq, project, args.dry_run)
    print()
    create_tables(bq, project, args.dry_run)
    print()
    create_gcs_bucket(gcs, project, args.dry_run)

    if not args.dry_run:
        print_next_steps(project)


if __name__ == "__main__":
    main()
