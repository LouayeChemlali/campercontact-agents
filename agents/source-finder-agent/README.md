# Source Finder Agent

This version merges the old Source Finder and Extractor responsibilities.

## Role

Input: one `profile_id` from the Gap Detector.

Process:
1. Read the target Campercontact profile from BigQuery only for query construction.
2. Build profile-specific search queries.
3. Search the approved Vertex AI Search datastore.
4. Deduplicate candidate URLs.
5. Fetch each candidate page, respecting robots.txt by default.
6. Extract lightweight external fields from HTML / JSON-LD / visible text.
7. Write one output table for Entity Matcher.

The output table does **not** duplicate the full Campercontact profile. It only stores `profile_id` as the join key.

## Output table

`primary_dataset.profile-info-external-sources`

Each row is one candidate external source for one profile/run.

## Local / Cloud Shell run

```bash
pip install -r requirements.txt
python main.py --profile_id YOUR_PROFILE_ID --export_csv
```

Use `--no_bigquery` to test without writing to BigQuery:

```bash
python main.py --profile_id YOUR_PROFILE_ID --export_csv --no_bigquery
```

## Cloud Run endpoint

```http
POST /run-source-finder
```

Example body:

```json
{
  "profile_id": "12345",
  "gap_detector_run_id": "gap-run-001",
  "page_size": 10,
  "max_candidate_urls": 20,
  "export_csv": false,
  "write_bigquery": true
}
```

## Environment variables

| Variable | Default | Purpose |
|---|---:|---|
| PROJECT_ID | project-62cd3637-0b98-4aa5-8d5 | GCP project |
| OUTPUT_DATASET | primary_dataset | Source Finder output dataset |
| QUEUE_DATASET | primary_dataset | Queue dataset written by Gap Detector |
| PROFILE_DATASET | primary_dataset | Dataset containing the clean profile table |
| PROFILE_TABLE | profile_master_sitecode_clean | Source profile table |
| OUTPUT_TABLE | profile-info-external-sources | Agent output table |
| QUEUE_TABLE | source_finder_queue | Pending profiles queue table |
| SERVING_CONFIG | existing Vertex AI Search config | Vertex AI Search serving config |
| DEFAULT_PAGE_SIZE | 10 | Search results per query |
| MAX_CANDIDATE_URLS | 20 | Max URLs extracted per run |
| RESPECT_ROBOTS_TXT | true | Respect robots.txt during direct fetch |
| USER_AGENT | CampercontactSourceFinder/1.0 | HTTP user agent |

## Deploy from source to Cloud Run

```bash
gcloud config set project project-62cd3637-0b98-4aa5-8d5

gcloud run deploy source-finder-agent \
  --source . \
  --region europe-west1 \
  --service-account source-finder-agent@project-62cd3637-0b98-4aa5-8d5.iam.gserviceaccount.com \
  --set-env-vars PROJECT_ID=project-62cd3637-0b98-4aa5-8d5,PROFILE_DATASET=primary_dataset,PROFILE_TABLE=profile_master_sitecode_clean,QUEUE_DATASET=primary_dataset,QUEUE_TABLE=source_finder_queue,OUTPUT_DATASET=primary_dataset,OUTPUT_TABLE=profile-info-external-sources \
  --no-allow-unauthenticated
```

Replace `PROFILE_TABLE=cc_profiles` with the real profile table.

## Required IAM for the Cloud Run service account

Minimum practical roles:

- BigQuery Data Viewer on the profile dataset/table
- BigQuery Data Editor on the output dataset/table
- BigQuery Job User on the project
- permission to call Discovery Engine / Vertex AI Search APIs

## Create output table

```bash
bq query --use_legacy_sql=false < sql/create_source_finder_profile_sources.sql
```
