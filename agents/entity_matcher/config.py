# env-based config for the entity matcher, table names and field comparison settings

import os
from dotenv import load_dotenv

load_dotenv()

# Keep defaults aligned with the already deployed Gap Detector and Source Finder.
BIGQUERY_PROJECT = os.getenv("BIGQUERY_PROJECT", os.getenv("PROJECT_ID", "project-62cd3637-0b98-4aa5-8d5"))
CC_TABLE = os.getenv("CC_TABLE", "primary_dataset.profile_master_sitecode_clean")
SOURCE_TABLE = os.getenv("SOURCE_TABLE", "primary_dataset.profile-info-external-sources")
GAP_TABLE = os.getenv("GAP_TABLE", "gap_detector_final.t10_gap_detector_output")
OUTPUT_TABLE = os.getenv("OUTPUT_TABLE", "entity_matcher_pipeline.entity_matcher_output")

# Source Finder may return useful page metadata even if direct HTML extraction failed,
# but v1 matching only compares extracted field values. Keep this strict by default.
SOURCE_SUCCESS_STATUSES = [
    s.strip()
    for s in os.getenv("SOURCE_SUCCESS_STATUSES", "success").split(",")
    if s.strip()
]

# Field configuration: one entry per field to compare.
FIELDS_CONFIG = [
    {
        "field": "email",
        "cc_field": "contact_details_email",
        "source_field": "source_email_found",
        "compare_type": "string_exact",
        "normalize": "email",
        "enabled": True,
    },
    {
        "field": "website",
        "cc_field": "contact_details_website",
        "source_field": "source_website_found",
        "compare_type": "string_exact",
        "normalize": "url",
        "enabled": True,
    },
    {
        "field": "name",
        "cc_field": "name",
        "cc_fallback": "title",
        "source_field": "source_name_found",
        "compare_type": "string_fuzzy",
        "threshold": 0.85,
        "enabled": True,
    },
    {
        "field": "city",
        "cc_field": "city",
        "source_field": "source_city_found",
        "compare_type": "string_fuzzy",
        "threshold": 0.85,
        "enabled": True,
    },
    {
        "field": "country",
        "cc_field": "country",
        "source_field": "source_country_found",
        "compare_type": "string_fuzzy",
        "threshold": 0.85,
        "enabled": True,
    },
    {
        "field": "address",
        # Special composite CC field, assembled in matcher.py from parts
        "cc_field": "__composite_address__",
        "cc_parts": ["address_street", "address_house_number", "address_zip_code"],
        "source_field": "source_address_found",
        "compare_type": "string_fuzzy",
        "threshold": 0.75,
        "enabled": True,
    },
    # TODO not included in v1, empty data in source or requires extraction
    # {"field": "phone", "cc_field": "contact_details_phone_number", "source_field": "source_phone_found",
    #  "compare_type": "string_exact", "normalize": "phone", "enabled": False},
    # {"field": "latitude", "cc_field": "latitude", "source_field": "source_latitude_found",
    #  "compare_type": "numeric_geo", "enabled": False},
    # {"field": "longitude", "cc_field": "longitude", "source_field": "source_longitude_found",
    #  "compare_type": "numeric_geo", "enabled": False},
    # {"field": "facilities", "cc_field": "facilities", "source_field": "source_facilities_text",
    #  "compare_type": "boolean", "enabled": False},
    # {"field": "price", "cc_field": "price_per_night", "source_field": "source_price_text",
    #  "compare_type": "numeric_directional", "enabled": False},
]

ACTIVE_FIELDS = [f for f in FIELDS_CONFIG if f["enabled"]]
