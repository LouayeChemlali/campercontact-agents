-- Optional mock tables so you can test the Hint Generator before Joey/Louaye finalize their outputs.
-- You can delete these later.

CREATE TABLE IF NOT EXISTS `project-62cd3637-0b98-4aa5-8d5.primary_dataset.mock_entity_matcher_results` (
  profile_id STRING,
  profile_name STRING,
  field_name STRING,
  current_value STRING,
  external_value STRING,
  source_url STRING,
  source_domain STRING,
  entity_match_score FLOAT64,
  verification_status STRING
);

CREATE TABLE IF NOT EXISTS `project-62cd3637-0b98-4aa5-8d5.primary_dataset.mock_ml_layer_results` (
  profile_id STRING,
  field_name STRING,
  recommendation_type STRING,
  prehint_score FLOAT64,
  posthint_score_est FLOAT64,
  ml_reason STRING
);

INSERT INTO `project-62cd3637-0b98-4aa5-8d5.primary_dataset.mock_entity_matcher_results`
(profile_id, profile_name, field_name, current_value, external_value, source_url, source_domain, entity_match_score, verification_status)
VALUES
('160668', 'Agriturismo Angeli Sognanti', 'images', '4 images', 'More images recommended', NULL, NULL, 1.00, 'internal_profile_signal'),
('160668', 'Agriturismo Angeli Sognanti', 'electricity', 'missing', 'electricity available', 'https://example.com/agriturismo-angeli-sognanti', 'example.com', 0.91, 'verified_match'),
('160668', 'Agriturismo Angeli Sognanti', 'description', 'short description', 'more complete description recommended', NULL, NULL, 1.00, 'internal_profile_signal');

INSERT INTO `project-62cd3637-0b98-4aa5-8d5.primary_dataset.mock_ml_layer_results`
(profile_id, field_name, recommendation_type, prehint_score, posthint_score_est, ml_reason)
VALUES
('160668', 'images', 'add_more_images', 12, 16, 'The profile currently has only 4 images. According to the profile score equation, adding more relevant images improves the completeness component.'),
('160668', 'electricity', 'missing_facility', 12, 14, 'Facility completeness contributes to the profile score. If electricity is confirmed, adding it can improve the facilities component.'),
('160668', 'description', 'improve_description', 12, 13, 'A more complete description improves the information completeness component of the profile score.');
