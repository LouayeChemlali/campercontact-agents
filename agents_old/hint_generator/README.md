# Hint Generator Prototype

This prototype generates Campercontact moderator hints from two inputs:

1. Entity Matcher output
2. ML Layer output

It uses a hybrid design:

- one field-level hint per missing/incomplete/actionable field
- one optional profile-level summary built from the field-level hints

The LLM is inside the Hint Generator. The ML layer provides the score-impact information that makes the hint more useful.

## Expected input tables

### Entity Matcher table

The prototype expects these columns:

```text
profile_id
profile_name
field_name
current_value
external_value
source_url
source_domain
entity_match_score
verification_status
```

### ML Layer table

The prototype expects these columns:

```text
profile_id
field_name
recommendation_type
prehint_score
posthint_score_est
ml_reason
```

The join key is:

```text
profile_id + field_name
```

## Output tables

The script writes to:

```text
location_data_campercontact.hint_field_results
location_data_campercontact.hint_profile_summaries
```

The `source_url_internal` field is stored for traceability, but the generated hint text does not show raw source URLs by default.

## Run in Cloud Shell

```bash
gcloud config set project project-62cd3637-0b98-4aa5-8d5
mkdir hint-generator-prototype
cd hint-generator-prototype
```

Upload/copy these files into the folder, then install:

```bash
pip install -r requirements.txt
```

Create the output tables:

```bash
bq query --use_legacy_sql=false < create_tables.sql
```

Optional: create mock input tables for testing:

```bash
bq query --use_legacy_sql=false < mock_input_tables.sql
```

Run a dry run for one profile:

```bash
python main.py --profile_id 160668 --dry_run
```

Run and insert into BigQuery:

```bash
python main.py --profile_id 160668
```

Run without Gemini, using deterministic fallback text:

```bash
USE_GEMINI=false python main.py --profile_id 160668 --dry_run
```

## Switch from mock tables to real tables

When the Entity Matcher and ML Layer tables are ready, run:

```bash
ENTITY_MATCHER_TABLE=real_entity_matcher_table \
ML_LAYER_TABLE=real_ml_layer_table \
python main.py --profile_id 160668 --dry_run
```

Or edit these constants in `main.py`:

```python
ENTITY_MATCHER_TABLE = "..."
ML_LAYER_TABLE = "..."
```
