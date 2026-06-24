# Campercontact Agents

This repository contains the final prototype for the Campercontact AI-supported data-quality pipeline. The project was developed as a modular agent pipeline that detects incomplete Campercontact profiles, searches for external evidence, matches candidate sources to profiles, generates moderator-facing hints, assigns confidence levels, and displays the results in a frontend.

## Project overview

The pipeline is designed to support Campercontact moderators by identifying profiles that may need improvement and by surfacing possible external evidence. The system does not automatically update Campercontact data. Instead, it produces reviewable hints that can be checked by a human moderator.

## Pipeline flow

```text
Gap Detector
    ↓
Source Finder
    ↓
Entity Matcher
    ↓
Hint Prioritization
    ↓
Hint Generator
    ↓
Confidence Agent
    ↓
BigQuery
    ↓
Frontend
```

## Repository structure

```text
campercontact-agents/
├── agents/
│   ├── confidence-agent/
│   ├── entity_matcher/
│   ├── gap-detector-agent/
│   ├── hint_generator/
│   └── source-finder-agent/
├── frontend_v2/
├── .env.example
├── .gitignore
├── README.md
└── campercontact logo.png
```

## Components

### Gap Detector

The Gap Detector identifies missing or incomplete fields in Campercontact profile data. Its output is used as the starting point for the rest of the pipeline.

### Source Finder

The Source Finder searches external camping and motorhome-related sources for possible profile evidence. The agent writes candidate source results to BigQuery.

### Entity Matcher

The Entity Matcher compares Campercontact profiles with external source candidates and checks whether they likely refer to the same real-world accommodation.

### Hint Prioritization

The prioritization layer ranks candidate hints based on relevance, expected usefulness, and anomaly-related priority signals.

### Hint Generator

The Hint Generator converts matched evidence into moderator-facing hints. These hints are written to BigQuery and later checked by the Confidence Agent.

### Confidence Agent

The Confidence Agent assigns a confidence score and decision to generated hints. Low-confidence hints are marked for manual review or hidden from direct use.

### Frontend

The frontend provides a simple interface to run the pipeline and inspect generated hints. It connects to the deployed backend services and reads the final confidence-scored hints from BigQuery.

## GCP setup

Main project:

```text
project-62cd3637-0b98-4aa5-8d5
```

Region:

```text
europe-west1
```

Main deployed services:

```text
gap-detector-agent
source-finder-agent
entity-matcher-agent
hint-generator-agent
confidence-agent
campercontact-frontend
```

Main BigQuery tables:

```text
gap_detector_final.t10_gap_detector_output
primary_dataset.source_finder_queue
primary_dataset.profile-info-external-sources
entity_matcher_pipeline.entity_matcher_output_v2
hint_prioritization.prioritized_hint_candidates_v1
primary_dataset.hint_field_results
primary_dataset.hint_confidence_results
```

## Environment variables

Use `.env.example` as the template for required environment variables.

```bash
cp .env.example .env
```

Do not commit `.env`, service account keys, or any credential files.

Typical environment variables include:

```text
PROJECT_ID
REGION
GAP_OUTPUT_TABLE
SOURCE_QUEUE_TABLE
SOURCE_OUTPUT_TABLE
ENTITY_MATCHER_OUTPUT_TABLE
PRIORITIZED_HINT_TABLE
HINT_OUTPUT_TABLE
CONFIDENCE_OUTPUT_TABLE
SOURCE_FINDER_URL
ENTITY_MATCHER_URL
HINT_GENERATOR_URL
CONFIDENCE_AGENT_URL
GD_URL
GD_AUTH
POLLING_INTERVAL_SECONDS
POLLING_TIMEOUT_SECONDS
PIPELINE_REQUEST_TIMEOUT_SECONDS
```

## Local setup

Each agent has its own dependencies. To run an agent locally, go into the relevant folder and install its requirements.

Example:

```bash
cd agents/gap-detector-agent
pip install -r requirements.txt
python main.py
```

Some services may use FastAPI/Uvicorn:

```bash
uvicorn main:app --host 0.0.0.0 --port 8080
```

The frontend can be run from the `frontend_v2` folder:

```bash
cd frontend_v2
pip install -r requirements.txt
python run.py
```

## Deployment pattern

The agents were deployed as separate Cloud Run services. A typical deployment command follows this pattern:

```bash
gcloud run deploy <service-name> \
  --source . \
  --region=europe-west1 \
  --set-env-vars PROJECT_ID=project-62cd3637-0b98-4aa5-8d5
```

Each service may require additional environment variables. Use `.env.example` and the relevant agent folder as reference.

## Final testing

The final environment was tested across multiple Campercontact profiles to check whether the full pipeline could run end-to-end. The tests confirmed that the deployed services can trigger each other in sequence and that the final confidence-scored hints are written to BigQuery and shown in the frontend.

One example profile used during the final frontend demonstration was:

```text
profile_id: 19060
profile_name: Parking
```

A successful run follows this sequence:

```text
Gap Detector → Source Finder → Entity Matcher → Hint Generator → Confidence Agent → Frontend
```

The frontend then displays the generated hints together with their confidence level and review decision.

## Notes and limitations

This repository represents a course prototype, not a production-ready system. The current version demonstrates that the modular pipeline can run end-to-end, but further work would be needed before real deployment.

Important future improvements include:

- stronger source validation,
- improved entity matching thresholds,
- more robust confidence scoring,
- better error handling and monitoring,
- clearer authentication and access management,
- broader testing across many Campercontact profiles,
- manual validation of generated hints before operational use.

## Security notes

Do not commit local environment files or credentials. Only `.env.example` should be committed as a template for required settings.

Files that should stay out of Git include:

```text
.env
.env.*
service-account.json
credentials.json
key.json
*.log
__pycache__/
venv/
.venv/
node_modules/
*.zip
```
