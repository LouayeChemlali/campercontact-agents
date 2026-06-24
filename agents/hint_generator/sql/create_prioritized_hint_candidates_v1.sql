CREATE SCHEMA IF NOT EXISTS `project-62cd3637-0b98-4aa5-8d5.hint_prioritization`;

CREATE TABLE IF NOT EXISTS `project-62cd3637-0b98-4aa5-8d5.hint_prioritization.prioritized_hint_candidates_v1` (
  hint_candidate_id STRING,
  gap_detector_run_id STRING,
  sitecode STRING,
  profile_id STRING,
  profile_name STRING,
  field_name STRING,
  current_value STRING,
  suggested_value STRING,
  source_url_internal STRING,
  source_domain_internal STRING,
  entity_match_score FLOAT64,
  verification_status STRING,
  recommendation_type STRING,
  prehint_score FLOAT64,
  posthint_score_est FLOAT64,
  score_delta FLOAT64,
  ml_reason STRING,
  baseline_priority_score FLOAT64,
  anomaly_review_tier STRING,
  anomaly_priority_bonus FLOAT64,
  integrated_priority_score FLOAT64,
  integrated_priority_label STRING,
  integrated_queue_rank INT64,
  gap_recommended_actions STRING,
  created_at TIMESTAMP
);
