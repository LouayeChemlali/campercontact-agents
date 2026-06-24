# Campercontact Agents

This repository contains the final prototype for the Campercontact AI-supported data-quality pipeline. The project was developed as a modular agent pipeline that detects incomplete Campercontact profiles, searches for external evidence, compares candidate source data with Campercontact profile data, generates moderator-facing hints, assigns confidence levels, and displays the results in a frontend.

## Project overview

The pipeline is designed to support Campercontact moderators by identifying profiles that may need improvement and by surfacing possible external evidence. The system does not automatically update Campercontact data. Instead, it produces reviewable hints that can be checked by a human moderator.

BigQuery is used as the shared data layer across the pipeline. The agents read from and write to BigQuery tables, while the frontend displays the final confidence-scored output.

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

The Source Finder searches external camping and motorhome-related sources for possible evidence related to Campercontact profiles. It writes candidate source results to BigQuery so that later agents can use them.

### Entity Matcher

The Entity Matcher compares Campercontact profile fields with the external candidate source data. It evaluates fields such as name, address, website, email, phone and location information, and classifies the comparison result using categories such as `MATCH`, `NEW_INFO`, `MISMATCH_INFO` or `NO_DATA`.

### Hint Prioritization

The prioritization layer ranks candidate hints based on relevance, expected usefulness, and priority signals. This helps decide which hints should be handled first.

### Hint Generator

The Hint Generator converts matched evidence and profile-level context into moderator-facing hints. It writes both field-level hint results and profile-level summaries to BigQuery.

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

Main BigQuery tables used by the final prototype:

```text
gap_detector_final.t10_gap_detector_output
primary_dataset.source_finder_queue
primary_dataset.profile-info-external-sources
entity_matcher_pipeline.entity_matcher_output
hint_prioritization.prioritized_hint_candidates_v1
primary_dataset.hint_field_results
primary_dataset.hint_profile_summaries
primary_dataset.hint_confidence_results
```

## Environment variables

Use `.env.example` as the template for required environment variables.

```bash
cp .env.example .env
```

Do not commit `.env`, service account keys, or credential files.

The project uses service-specific environment variables. Some services use generic names such as `OUTPUT_TABLE`, while others use URL, authentication, polling, and trigger flags. Check the relevant agent folder and `.env.example` before deploying a service.

Common configuration categories include:

```text
PROJECT_ID
REGION
OUTPUT_TABLE
service URL variables such as SOURCE_FINDER_URL, ENTITY_MATCHER_URL, HINT_GENERATOR_URL and CONFIDENCE_AGENT_URL
authentication flags such as SOURCE_FINDER_AUTH, ENTITY_MATCHER_AUTH, HINT_GENERATOR_AUTH and CONFIDENCE_AGENT_AUTH
auto-trigger flags such as AUTO_TRIGGER_SOURCE_FINDER, AUTO_TRIGGER_ENTITY_MATCHER, AUTO_TRIGGER_HINT_GENERATOR and AUTO_TRIGGER_CONFIDENCE_AGENT
frontend variables such as GD_URL, GD_AUTH, POLLING_INTERVAL_SECONDS, POLLING_TIMEOUT_SECONDS and PIPELINE_REQUEST_TIMEOUT_SECONDS
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
