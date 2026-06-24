CREATE TABLE IF NOT EXISTS `project-62cd3637-0b98-4aa5-8d5.primary_dataset.profile-info-external-sources` (
  source_finder_run_id STRING,
  gap_detector_run_id STRING,
  profile_id STRING,

  query_used STRING,
  source_domain STRING,
  source_url STRING,
  page_title STRING,
  snippet STRING,
  search_rank INT64,

  source_name_found STRING,
  source_address_found STRING,
  source_city_found STRING,
  source_country_found STRING,
  source_phone_found STRING,
  source_email_found STRING,
  source_website_found STRING,
  source_latitude_found STRING,
  source_longitude_found STRING,
  source_facilities_text STRING,
  source_opening_text STRING,
  source_price_text STRING,
  source_page_text_excerpt STRING,

  source_type STRING,
  extraction_status STRING,
  extraction_error STRING,
  created_at TIMESTAMP
);
