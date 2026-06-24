from __future__ import annotations

from typing import Any, Dict

from google.cloud import bigquery

from .config import PROFILE_DATASET, PROFILE_TABLE, PROJECT_ID


def get_target_profile(profile_id: str) -> Dict[str, Any]:
    """Read one Campercontact profile using sitecode as the single identity key."""
    client = bigquery.Client(project=PROJECT_ID)
    table_id = f"{PROJECT_ID}.{PROFILE_DATASET}.{PROFILE_TABLE}"

    query = f"""
        SELECT
            CAST(sitecode AS STRING) AS id,
            CAST(sitecode AS STRING) AS sitecode,

            COALESCE(
                NULLIF(name, ''),
                NULLIF(title, ''),
                NULLIF(location_name, '')
            ) AS name,

            COALESCE(
                NULLIF(address_street, ''),
                NULLIF(straat, '')
            ) AS address_street,

            COALESCE(
                NULLIF(address_house_number, ''),
                NULLIF(huisnummer, '')
            ) AS address_house_number,

            COALESCE(
                NULLIF(address_zip_code, ''),
                NULLIF(postcode, '')
            ) AS zipcode,

            COALESCE(
                NULLIF(city, ''),
                NULLIF(plaats, '')
            ) AS city,

            COALESCE(
                NULLIF(country, ''),
                NULLIF(land, '')
            ) AS country,

            COALESCE(
                NULLIF(contact_details_phone_number, ''),
                NULLIF(contact_phone, '')
            ) AS contact,

            COALESCE(
                NULLIF(contact_details_email, ''),
                NULLIF(contact_email, ''),
                NULLIF(host_email, '')
            ) AS email,

            COALESCE(
                NULLIF(contact_details_website, ''),
                NULLIF(contact_url, ''),
                NULLIF(urls_url_en, ''),
                NULLIF(urls_url_nl, '')
            ) AS website,

            CAST(latitude AS STRING) AS latitude,
            CAST(longitude AS STRING) AS longitude,
            CAST(latitude_num AS STRING) AS latitude_num,
            CAST(longitude_num AS STRING) AS longitude_num

        FROM `{table_id}`
        WHERE CAST(sitecode AS STRING) = @profile_id
        LIMIT 1
    """

    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("profile_id", "STRING", str(profile_id))
        ]
    )

    rows = list(client.query(query, job_config=job_config).result())

    if not rows:
        raise ValueError(f"No Campercontact profile found for sitecode={profile_id}")

    profile = dict(rows[0].items())

    street = (profile.get("address_street") or "").strip()
    number = (profile.get("address_house_number") or "").strip()
    combined_address = " ".join(part for part in [street, number] if part).strip()

    if combined_address:
        profile["address_house_number"] = combined_address

    return profile
