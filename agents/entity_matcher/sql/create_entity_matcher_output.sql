CREATE SCHEMA IF NOT EXISTS `project-62cd3637-0b98-4aa5-8d5.entity_matcher_pipeline`;

CREATE TABLE IF NOT EXISTS `project-62cd3637-0b98-4aa5-8d5.entity_matcher_pipeline.entity_matcher_output` (
  profile_id STRING,
  profile_name STRING,
  field_name STRING,
  current_value STRING,
  external_value STRING,
  source_url STRING,
  source_domain STRING,
  matched_source_url STRING,
  matched_source_domain STRING,
  matched_field STRING,
  current_campercontact_value STRING,
  external_source_value STRING,
  source_title STRING,
  source_snippet STRING,
  entity_match_score FLOAT64,
  verification_status STRING,
  comparison_type STRING,
  num_sources INT64,
  source_finder_run_id STRING,
  gap_detector_run_id STRING,
  gap_recommended_actions STRING,
  run_timestamp TIMESTAMP
);
