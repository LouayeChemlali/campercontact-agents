import os
from datetime import datetime, timezone
from urllib.parse import urlparse

from dotenv import load_dotenv
from google.cloud import bigquery
from elasticsearch import Elasticsearch


load_dotenv()

PROJECT_ID = os.getenv("GCP_PROJECT_ID")
DATASET = os.getenv("BQ_DATASET")
INPUT_TABLE = os.getenv("INPUT_TABLE")
OUTPUT_TABLE = os.getenv("OUTPUT_TABLE")
ALLOWLIST_TABLE = os.getenv("ALLOWLIST_TABLE", "source_allowlist")

ELASTIC_URL = os.getenv("ELASTIC_URL")
ELASTIC_API_KEY = os.getenv("ELASTIC_API_KEY")
ELASTIC_INDEX = os.getenv("ELASTIC_INDEX")

bq_client = bigquery.Client(project=PROJECT_ID)

elastic_client = Elasticsearch(
    ELASTIC_URL,
    api_key=ELASTIC_API_KEY,
)


def clean(value):
    if value is None:
        return None

    value = str(value).strip()

    if value == "":
        return None

    return value


def normalize_domain(value):
    """
    Converts URLs/domains into a comparable domain format.

    Examples:
    https://www.example.com/page -> example.com
    www.example.com -> example.com
    example.com -> example.com
    """
    if not value:
        return None

    value = str(value).strip().lower()

    if not value.startswith("http://") and not value.startswith("https://"):
        value = "https://" + value

    domain = urlparse(value).netloc.lower()

    if domain.startswith("www."):
        domain = domain[4:]

    return domain or None


def build_queries(profile):
    queries = []

    name = clean(profile.get("title"))
    address_house_number = clean(profile.get("address_house_number"))
    zipcode = clean(profile.get("address_zip_code"))
    city = clean(profile.get("city"))
    country = clean(profile.get("country"))
    contact = clean(profile.get("contact_details_phone_number"))
    email = clean(profile.get("contact_details_email"))
    website = clean(profile.get("contact_details_website"))

    # Strong identity queries
    if name and city and country:
        queries.append(f"{name} {city} {country}")

    if name and zipcode and country:
        queries.append(f"{name} {zipcode} {country}")

    if name and address_house_number and city:
        queries.append(f"{name} {address_house_number} {city}")

    if name and address_house_number and zipcode:
        queries.append(f"{name} {address_house_number} {zipcode}")

    if name and city and zipcode:
        queries.append(f"{name} {city} {zipcode}")

    # Address/location fallback queries
    if address_house_number and zipcode and city:
        queries.append(f"{address_house_number} {zipcode} {city}")

    if zipcode and city and country:
        queries.append(f"{zipcode} {city} {country}")

    if city and country:
        queries.append(f"camping {city} {country}")

    if zipcode and country:
        queries.append(f"camping {zipcode} {country}")

    # Type-enhanced queries
    if name and city:
        queries.append(f"{name} camping {city}")

    if name and country:
        queries.append(f"{name} campsite {country}")

    if name and city and country:
        queries.append(f"{name} campsite {city} {country}")

    # Contact / email / website queries
    if website:
        queries.append(website)

    if name and website:
        queries.append(f"{name} {website}")

    if contact:
        queries.append(f"{contact} camping")

    if name and contact:
        queries.append(f"{name} {contact}")

    if email:
        queries.append(email)

    if email and name:
        queries.append(f"{name} {email}")

    if email and "@" in email:
        email_domain = email.split("@")[-1]
        queries.append(email_domain)

    # Broad fallback queries
    if name:
        queries.append(f"{name} camping")

    if name and country:
        queries.append(f"{name} {country}")

    if name and city:
        queries.append(f"{name} {city}")

    # Remove duplicates while preserving order
    queries = list(dict.fromkeys(queries))

    return queries[:10]


def read_profiles(limit=5):
    query = f"""
        SELECT 
            id,
            sitecode,
            title,
            address_house_number,
            address_zip_code,
            city,
            country,
            contact_details_phone_number,
            contact_details_email,
            contact_details_website
        FROM `{PROJECT_ID}.{DATASET}.{INPUT_TABLE}`
        WHERE id IS NOT NULL
        LIMIT {limit}
    """

    rows = bq_client.query(query).result()
    return [dict(row) for row in rows]


def read_allowlist():
    """
    Reads approved domains from BigQuery.

    Expected source_allowlist columns:
    - domain
    - source_type
    - priority
    - crawl_allowed
    """
    query = f"""
        SELECT
            domain,
            source_type,
            priority
        FROM `{PROJECT_ID}.{DATASET}.{ALLOWLIST_TABLE}`
        WHERE crawl_allowed = TRUE
    """

    rows = bq_client.query(query).result()

    allowlist = {}

    for row in rows:
        domain = normalize_domain(row["domain"])

        if not domain:
            continue

        allowlist[domain] = {
            "source_type": row.get("source_type") or "allowlisted_web",
            "priority": row.get("priority") or 1,
        }

    return allowlist


def extract_url_from_hit(source):
    """
    Elastic crawler field names may differ depending on how the index was created.
    This tries the most common URL fields.
    """
    return (
        source.get("url")
        or source.get("page_url")
        or source.get("source_url")
        or source.get("web_url")
    )


def extract_title_from_hit(source):
    return (
        source.get("title")
        or source.get("page_title")
        or source.get("name")
        or "No title"
    )


def search_elastic(query_text, allowed_domains, size=10):
    """
    Searches the Elastic index.

    Important:
    This assumes Elastic already contains crawled/indexed pages
    from your 50-100 approved websites.
    """

    try:
        response = elastic_client.search(
            index=ELASTIC_INDEX,
            size=size,
            query={
                "multi_match": {
                    "query": query_text,
                    "fields": [
                        "title^3",
                        "page_title^3",
                        "body_content",
                        "content",
                        "text",
                        "url^2",
                        "domain^2",
                    ],
                    "fuzziness": "AUTO",
                }
            },
        )

        hits = response.get("hits", {}).get("hits", [])

        filtered_hits = []

        for hit in hits:
            source = hit.get("_source", {})
            url = extract_url_from_hit(source)

            if not url:
                continue

            domain = normalize_domain(source.get("domain") or url)

            if domain in allowed_domains:
                filtered_hits.append(hit)

        return filtered_hits

    except Exception as e:
        print(f"[ELASTIC ERROR] Query: {query_text} → {e}")
        return []


def make_candidate_rows(profile, query_text, elastic_hits, allowed_domains):
    rows = []
    profile_id = profile.get("id")

    for hit in elastic_hits:
        source = hit.get("_source", {})

        url = extract_url_from_hit(source)
        title = extract_title_from_hit(source)
        domain = normalize_domain(source.get("domain") or url)

        if not url or not domain:
            continue

        allowlist_info = allowed_domains.get(domain, {})

        rows.append({
            "accommodation_id": str(profile_id),
            "source_url": url,
            "source_domain": domain,
            "source_type": allowlist_info.get("source_type", "allowlisted_web"),
            "retrieval_score": float(hit.get("_score", 0.0)),
            "fetch_status": "found",
            "matched_query": query_text,
            "notes": f"Elastic result: {title}",
            "created_at": datetime.now(timezone.utc).isoformat(),
        })

    return rows


def write_candidates(rows):
    if not rows:
        print("No candidate rows to write.")
        return

    table_id = f"{PROJECT_ID}.{DATASET}.{OUTPUT_TABLE}"

    print(f"Writing {len(rows)} rows to BigQuery table {table_id}...")

    errors = bq_client.insert_rows_json(table_id, rows)

    if errors:
        print("BigQuery insert errors:", errors)
        raise RuntimeError(errors)

    print("Successfully written to BigQuery.")


def main():
    profiles = read_profiles(limit=5)
    allowed_domains = read_allowlist()

    print(f"Loaded {len(profiles)} profiles.")
    print(f"Loaded {len(allowed_domains)} allowed domains.")

    all_candidate_rows = []

    for profile in profiles:
        print(f"\n--- Processing profile: {profile.get('title')} (ID: {profile.get('id')}) ---")

        queries = build_queries(profile)[:5]

        print(f"Generated queries: {queries}")

        for query_text in queries:
            print(f"Searching Elastic for: {query_text}")

            elastic_hits = search_elastic(
                query_text=query_text,
                allowed_domains=allowed_domains,
                size=10,
            )

            print(f"Elastic hits after allowlist filtering: {len(elastic_hits)}")

            candidate_rows = make_candidate_rows(
                profile=profile,
                query_text=query_text,
                elastic_hits=elastic_hits,
                allowed_domains=allowed_domains,
            )

            print(f"Candidate rows created: {len(candidate_rows)}")

            all_candidate_rows.extend(candidate_rows)

    write_candidates(all_candidate_rows)

    print(f"\nInserted {len(all_candidate_rows)} source candidates.")


if __name__ == "__main__":
    main()