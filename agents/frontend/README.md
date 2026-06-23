# Campercontact Moderator Tools

Internal web tool for Campercontact moderators. Enter one or more profile IDs, trigger the data quality pipeline, and review field-level improvement hints.

## Requirements

- Python 3.11 or later (`python --version` to check)
- Google Cloud Application Default Credentials configured on the host
- Access to the `project-62cd3637-0b98-4aa5-8d5` BigQuery project

## Setup

**1. Create and activate a virtual environment:**

```
python -m venv venv
venv\Scripts\activate     # Windows
source venv/bin/activate  # macOS / Linux
```

**2. Install dependencies:**

```
pip install -r requirements.txt
```

**3. Copy `.env.example` to `.env` and fill in the values:**

```
cp .env.example .env
```

Key values to set:
- `GD_URL`: the Cloud Run URL of the deployed Gap Detector service
- `FLASK_SECRET_KEY`: any random string, used for Flask session signing

`FLASK_APP=app.py` is already in `.env.example`. Flask reads it automatically when the `.env` file is loaded. You do not need to set it manually.

**4. Authenticate with Google Cloud:**

```
gcloud auth application-default login
```

**5. Verify the BigQuery connection before starting the app:**

```python
python -c "
from google.cloud import bigquery
client = bigquery.Client(project='project-62cd3637-0b98-4aa5-8d5')
list(client.list_tables('primary_dataset'))
print('BigQuery connection OK')
"
```

If this prints `BigQuery connection OK`, you are ready to run. Any error here is a credentials or project access problem to fix before proceeding.

## Running

```
cd agents/frontend
python run.py
```

This opens the browser automatically at `http://127.0.0.1:5050`.

For hot-reload during development, use Flask directly:

```
flask --debug run
```

## Project structure

```
agents/frontend/
  app.py                  Flask routes
  config.py               Loads and validates environment variables
  bigquery_client.py      All BigQuery reads, parameterized queries only
  pipeline_client.py      HTTP call to the Gap Detector Cloud Run service
  run.py                  Dev launcher: starts Flask and opens the browser
  templates/
    base.html             Shared header, footer, Tailwind CDN link
    index.html            Profile ID input form and recently processed list
    queue.html            ML-ranked priority queue with batch run support
    results.html          Per-profile hint cards with live polling
    profile.html          Single profile lookup (no pipeline run)
  static/
    css/custom.css        Small additions Tailwind CDN cannot do inline
    js/poll.js            Polling logic for the results page
    img/logo.png          Campercontact logo
  requirements.txt
  .env.example
  README.md
```

## Pages

| Page | URL | Description |
|------|-----|-------------|
| New run | `/` | Enter profile IDs and trigger the pipeline |
| Queue | `/queue` | ML-ranked priority queue from BigQuery |
| Results | `/results/<run_id>` | Live polling view for an active pipeline run |
| Profile | `/profile/<id>` | Most recent hints for a single profile |

## Pipeline flow

The frontend triggers the pipeline by POSTing profile IDs to the Gap Detector Cloud Run service. The Gap Detector calls Source Finder, Entity Matcher, and Hint Generator in sequence. Results land in BigQuery and the results page polls `/api/status` until all profiles are ready.

## Security and deployment

This tool has no authentication. It is intended to run on localhost or behind a private network (VPN, Cloud Run with IAM, or similar). Do not expose it to the public internet. Anyone who can reach the URL can trigger the pipeline and read hint data.

Before deploying beyond localhost, add at minimum an IP allowlist or deploy behind Google Cloud IAP.
