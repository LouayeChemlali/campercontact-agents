import os

PROJECT_ID = os.getenv("PROJECT_ID", "project-62cd3637-0b98-4aa5-8d5")

# Clean input profile table. Uses sitecode as the single identity key.
PROFILE_DATASET = os.getenv("PROFILE_DATASET", "primary_dataset")
PROFILE_TABLE = os.getenv("PROFILE_TABLE", "profile_master_sitecode_clean")

# Queue table written by Gap Detector and consumed by Source Finder.
# Keep this separate from the output table so changing the output dataset
# does not accidentally move the queue reader.
QUEUE_DATASET = os.getenv("QUEUE_DATASET", "primary_dataset")
QUEUE_TABLE = os.getenv("QUEUE_TABLE", "source_finder_queue")

# Output table for Source Finder results.
# This should match sql/create_source_finder_profile_sources.sql.
OUTPUT_DATASET = os.getenv("OUTPUT_DATASET", os.getenv("BQ_DATASET", "primary_dataset"))
OUTPUT_TABLE = os.getenv("OUTPUT_TABLE", "profile-info-external-sources")

SERVING_CONFIG = os.getenv(
    "SERVING_CONFIG",
    "projects/240305144830/locations/global/collections/default_collection/"
    "engines/campercontact-source-finde_1778683007390/servingConfigs/default_search",
)

SEARCH_API_VERSION = os.getenv("SEARCH_API_VERSION", "v1alpha")
DEFAULT_PAGE_SIZE = int(os.getenv("DEFAULT_PAGE_SIZE", "10"))
MAX_CANDIDATE_URLS = int(os.getenv("MAX_CANDIDATE_URLS", "20"))
SEARCH_TIMEOUT_SECONDS = int(os.getenv("SEARCH_TIMEOUT_SECONDS", "30"))
FETCH_TIMEOUT_SECONDS = int(os.getenv("FETCH_TIMEOUT_SECONDS", "20"))

RESPECT_ROBOTS_TXT = os.getenv("RESPECT_ROBOTS_TXT", "true").lower() == "true"
USER_AGENT = os.getenv(
    "USER_AGENT",
    "CampercontactSourceFinder/1.0 (+https://www.campercontact.com)",
)

CSV_EXPORT_DIR = os.getenv("CSV_EXPORT_DIR", "/tmp")

ALLOWED_SOURCE_DOMAINS = os.getenv(
    "ALLOWED_SOURCE_DOMAINS",
    "campspace.com,park4night.com,alanrogers.com,camperstop.com,"
    "jetcamp.com,stellplatz.info,caramaps.com,campingcarpark.com,"
    "pitchup.com,camping.info,eurocampings.co.uk,suncamp.co.uk,"
    "campingdirect.com,campsites.co.uk,ukcampsite.co.uk,pincamp.de"
)
