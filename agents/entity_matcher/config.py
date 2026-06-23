import os
from dotenv import load_dotenv

load_dotenv()

BIGQUERY_PROJECT = os.environ["BIGQUERY_PROJECT"]
CC_TABLE = os.environ["CC_TABLE"]
SOURCE_TABLE = os.environ["SOURCE_TABLE"]
GAP_TABLE = os.environ["GAP_TABLE"]
OUTPUT_TABLE = os.environ["OUTPUT_TABLE"]

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
        # Special composite CC field — assembled in matcher.py from parts
        "cc_field": "__composite_address__",
        "cc_parts": ["address_street", "address_house_number", "address_zip_code"],
        "source_field": "source_address_found",
        "compare_type": "string_fuzzy",
        "threshold": 0.75,
        "enabled": True,
    },
]

ACTIVE_FIELDS = [f for f in FIELDS_CONFIG if f["enabled"]]
