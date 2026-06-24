-- Output table 1: one row per field-level/action-level hint
CREATE TABLE IF NOT EXISTS `project-62cd3637-0b98-4aa5-8d5.primary_dataset.hint_field_results` (
  hint_id STRING,
  gap_detector_run_id STRING,
  profile_id STRING,
  profile_name STRING,
  field_name STRING,
  recommendation_type STRING,

  current_value STRING,
  suggested_value STRING,

  prehint_score FLOAT64,
  posthint_score_est FLOAT64,
  score_delta FLOAT64,
  ml_reason STRING,

  hint_text STRING,
  suggested_action STRING,

  -- Stored internally for traceability. Do not show raw URLs/snippets in the moderator-facing hint by default.
  source_url_internal STRING,
  source_domain_internal STRING,

  entity_match_score FLOAT64,
  verification_status STRING,

  created_at TIMESTAMP
);

-- Output table 2: one row per profile-level summary, built from the field-level hints
CREATE TABLE IF NOT EXISTS `project-62cd3637-0b98-4aa5-8d5.primary_dataset.hint_profile_summaries` (
  summary_id STRING,
  gap_detector_run_id STRING,
  profile_id STRING,
  profile_name STRING,

  profile_summary_text STRING,
  top_actions ARRAY<STRING>,

  total_estimated_score_delta FLOAT64,
  prehint_score FLOAT64,
  posthint_score_est FLOAT64,

  number_of_field_hints INT64,
  created_at TIMESTAMP
);
