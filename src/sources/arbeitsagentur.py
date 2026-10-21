"""
Day 11 — JobRadar Germany
Module: Bundesagentur Job Search with Employer-Link Preservation

THEORY:
An API adapter normalizes external records into a shared data schema.

If an adapter drops useful fields, such as an employer application
URL, later components cannot verify those fields.

This module:
1. Builds rotating searches across our 43 job-role families.
2. Queries Munich and Augsburg.
3. Handles searches returning zero results.
4. Preserves application URLs supplied by the job source.
5. Preserves original publication dates and employment types.
6. Removes duplicate vacancies using source reference IDs.

IMPORTANT:
Preserving a URL does not prove the vacancy is active.
Our separate ATS verifier will check supported employer links.

PRIVACY:
This module does not read or transmit the user's CV.
"""

import argparse
from datetime import date
from itertools import zip_longest
from urllib.parse import quote

import requests

from src.services.job_filter import load_roles


# ============================================================
# CONFIGURATION
# ============================================================

API_URL = (
    "https://rest.arbeitsagentur.de"
    "/jobboerse/jobsuche-service/pc/v6/jobs"
)

API_KEY = "jobboerse-jobsuche"

# Initial geographic search coverage.
SEARCH_LOCATIONS = {
    "München": 40,
    "Augsburg": 35,
}

# Two important searches run every day.
CORE_SEARCH_TERMS = (
    "Data Analyst",
    "Business Analyst",
)

# Additional role families rotate daily.
ROTATING_ROLES_PER_DAY = 6

# Different source versions can use different field names.
EXTERNAL_URL_FIELDS = (
    "externeUrl",
    "externeURL",
    "externalUrl",
    "external_url",
    "applyUrl",
    "apply_url",
)


# ============================================================
# STEP 1 — LOAD CONFIGURED JOB ROLES
# ============================================================

def ordered_role_families() -> list[dict]:
    """
    Load and interleave Priority A, B and C job families.

    This allows the search rotation to consider strong,
    related and growth opportunities.
    """

    roles = load_roles()

    groups = []

    for priority in (
        "priority_a",
        "priority_b",
        "priority_c",
    ):

        groups.append([
            {
                "role": title,
                "alternatives": alternatives,
                "priority": priority,
            }
            for title, alternatives in roles[priority].items()
        ])

    return [
        family
        for row in zip_longest(*groups)
        for family in row
        if family is not None
    ]


def choose_search_term(
    family: dict,
    run_date: date,
) -> str:
    """
    Rotate the main job title and its alternatives.

    English and German variations are selected
    across different calendar weeks.
    """

    terms = [
        family["role"],
        *family["alternatives"],
    ]

    index = (
        run_date.toordinal() // 7
    ) % len(terms)

    return terms[index]


def build_search_plan(
    run_date: date | None = None,
) -> tuple[list[dict], int]:
    """
    Generate the daily job-search plan.

    Two core terms are always searched.

    Six additional role families rotate each day.
    """

    run_date = run_date or date.today()

    families = ordered_role_families()

    plan = [
        {
            "role": name,
            "query": name,
            "priority": "core",
        }
        for name in CORE_SEARCH_TERMS
    ]

    rotation = [
        family
        for family in families
        if family["role"] not in CORE_SEARCH_TERMS
    ]

    if rotation:

        start = (
            run_date.toordinal()
            * ROTATING_ROLES_PER_DAY
        ) % len(rotation)

        count = min(
            ROTATING_ROLES_PER_DAY,
            len(rotation),
        )

        for offset in range(count):

            family = rotation[
                (start + offset) % len(rotation)
            ]

            plan.append({
                "role": family["role"],
                "query": choose_search_term(
                    family,
                    run_date,
                ),
                "priority": family["priority"],
            })

    return plan, len(families)


# ============================================================
# STEP 2 — HANDLE API RESPONSES
# ============================================================

def parse_search_response(
    payload: dict,
) -> list[dict]:
    """
    Extract job records from the API response.

    THEORY:
    An API response can be valid even when it contains
    zero vacancies.

    Missing job-list fields with an explicit zero count
    are treated as normal empty results.

    Unexpected response formats raise ValueError.
    """

    if not isinstance(payload, dict):

        raise ValueError(
            "API response must be a JSON object."
        )

    if "ergebnisliste" in payload:

        jobs = payload["ergebnisliste"]

    elif "stellenangebote" in payload:

        jobs = payload["stellenangebote"]

    else:

        try:
            total = int(
                payload.get("maxErgebnisse")
            )

        except (TypeError, ValueError):
            total = None

        if total == 0:
            return []

        raise ValueError(
            "Unrecognized response structure: "
            + str(list(payload.keys()))
        )

    if (
        jobs is None
        and payload.get("maxErgebnisse") in (0, "0")
    ):
        return []

    if not isinstance(jobs, list):

        raise ValueError(
            "API job list must be a list."
        )

    return jobs


# ============================================================
# STEP 3 — EXTRACT JOB LOCATION
# ============================================================

def extract_location(job: dict) -> str:
    """
    Extract the city from supported API formats.

    Never invent a location when the source
    does not provide one.
    """

    locations = job.get(
        "stellenlokationen"
    )

    if isinstance(locations, list):

        for item in locations:

            if not isinstance(item, dict):
                continue

            address = item.get("adresse")

            if (
                isinstance(address, dict)
                and address.get("ort")
            ):
                return str(address["ort"])

            if item.get("ort"):
                return str(item["ort"])

    old_location = job.get("arbeitsort")

    if (
        isinstance(old_location, dict)
        and old_location.get("ort")
    ):
        return str(old_location["ort"])

    return "Location not specified"


# ============================================================
# STEP 4 — PRESERVE APPLICATION URLS
# ============================================================

def source_application_url(
    job: dict,
) -> str | None:
    """
    Find an external application URL supplied by the API.

    Search known source fields in priority order.

    IMPORTANT:
    We preserve the URL exactly as provided.

    We do not:
    - Invent an employer careers page.
    - Assume the URL is active.
    - Automatically submit any application.

    Our employer verifier checks supported ATS links later.
    """

    for field in EXTERNAL_URL_FIELDS:

        value = job.get(field)

        if (
            isinstance(value, str)
            and value.strip()
        ):
            return value.strip()

    return None


# ============================================================
# STEP 5 — NORMALIZE JOB RECORDS
# ============================================================

def normalize_job(
    job: dict,
) -> dict | None:
    """
    Convert a Bundesagentur vacancy into JobRadar format.

    IMPORTANT CHANGE:
    We now preserve the original external application URL
    instead of discarding it during normalization.

    We also preserve:
    - Original publication date
    - Available employment-type information
    - Job description, if present

    Missing values remain None.
    """

    if not isinstance(job, dict):
        return None

    reference = (
        job.get("referenznummer")
        or job.get("refnr")
    )

    title = (
        job.get("stellenangebotsTitel")
        or job.get("titel")
        or job.get("beruf")
    )

    company = (
        job.get("firma")
        or job.get("arbeitgeber")
    )

    if not reference or not title:
        return None

    reference = str(reference).strip()
    title = str(title).strip()

    if not reference or not title:
        return None

    # Preserve the external URL if the source provides it.
    external_url = source_application_url(job)

    # Preserve the original advertisement date separately.
    original_date = (
        job.get("ersteVeroeffentlichungsdatum")
        or job.get("datumErsteVeroeffentlichung")
    )

    published_date = (
        job.get("datumErsteVeroeffentlichung")
        or job.get("aktuelleVeroeffentlichungsdatum")
    )

    # Preserve descriptions only when the source
    # provides actual text.
    description = job.get(
        "stellenangebotsBeschreibung"
    )

    if not isinstance(description, str):
        description = None

    # Preserve employment information if available.
    work_models = job.get(
        "arbeitszeitmodelle"
    )

    if not isinstance(work_models, list):
        work_models = None

    return {
        "title": title,

        "company_name": (
            str(company)
            if company
            else "Unknown"
        ),

        "location": extract_location(job),

        # Remote eligibility still requires confirmation.
        "remote": None,

        # Official Bundesagentur job-detail link.
        "url": (
            "https://www.arbeitsagentur.de"
            "/jobsuche/jobdetail/"
            + quote(reference, safe="")
        ),

        "source": "Bundesagentur für Arbeit",

        "source_id": reference,

        "published_at": published_date,

        "original_published_at": original_date,

        # NEW: preserve the employer application URL.
        "application_url": external_url,

        # Additional information for later verification.
        "description": description,

        "arbeitszeitmodelle": work_models,
    }


# ============================================================
# STEP 6 — SEARCH THE API
# ============================================================

def search_jobs(
    keyword: str,
    location: str,
    radius: int,
) -> list[dict]:
    """
    Search one job keyword and location.

    The API currently returns up to 25 results
    per request in our configuration.
    """

    response = requests.get(
        API_URL,
        headers={
            "X-API-Key": API_KEY,
            "Accept": "application/json",
            "User-Agent": "JobRadarGermany/1.0",
        },
        params={
            "was": keyword,
            "wo": location,
            "umkreis": radius,
            "angebotsart": 1,
            "veroeffentlichtseit": 30,
            "page": 1,
            "size": 25,
        },
        timeout=25,
    )

    response.raise_for_status()

    raw_jobs = parse_search_response(
        response.json()
    )

    results = []

    for item in raw_jobs:

        normalized = normalize_job(
            item
        )

        if normalized is not None:

            results.append(normalized)

    return results


# ============================================================
# STEP 7 — EXECUTE THE ROTATING SEARCH PLAN
# ============================================================

def fetch_german_jobs() -> list[dict]:
    """
    Execute today's search plan.

    Includes:
    - Search statistics
    - Duplicate removal
    - Error handling
    - Application URL preservation

    The search continues when an individual
    API request fails.
    """

    plan, role_count = build_search_plan()

    print(
        "Configured role families:",
        role_count,
    )

    print(
        "Daily search terms:",
        len(plan),
    )

    unique_jobs = []
    references = set()

    nonempty = 0
    empty = 0
    failed = 0

    for item in plan:

        for location, radius in SEARCH_LOCATIONS.items():

            print(
                f"Searching: {item['query']} | "
                f"{location} | {radius} km"
            )

            try:

                jobs = search_jobs(
                    item["query"],
                    location,
                    radius,
                )

            except (
                requests.RequestException,
                ValueError,
            ) as error:

                failed += 1

                print(
                    "WARNING: Search failed:",
                    error,
                )

                continue

            if not jobs:

                empty += 1

                print(
                    "No matching vacancies found."
                )

                continue

            nonempty += 1

            print(
                "Results received:",
                len(jobs),
            )

            for job in jobs:

                reference = job["source_id"]

                if reference not in references:

                    references.add(reference)

                    unique_jobs.append(job)

    print(
        "\n========== SEARCH STATISTICS =========="
    )

    print("Searches with vacancies:", nonempty)
    print("Searches with zero vacancies:", empty)
    print("Failed searches:", failed)
    print("Completed searches:", nonempty + empty)
    print("Unique jobs collected:", len(unique_jobs))

    # NEW: measure actual external URL availability.
    links_found = sum(
        bool(job["application_url"])
        for job in unique_jobs
    )

    print(
        "Jobs with source application URLs:",
        links_found,
    )

    if nonempty + empty == 0:

        raise RuntimeError(
            "All job searches failed."
        )

    return unique_jobs


# ============================================================
# STEP 8 — OFFLINE TESTS
# ============================================================

def run_self_test() -> None:
    """
    Test the updated API adapter without Internet access.

    Tests:
    1. Empty search responses
    2. External application URL preservation
    3. Alternative API field spelling
    4. Missing application URLs
    5. Original publication date and employment type
    """

    # TEST 1 — Zero results.
    payload = {
        "maxErgebnisse": "0",
        "page": 1,
        "size": 25,
    }

    assert parse_search_response(payload) == []

    print(
        "PASS: Zero-result searches handled"
    )

    # Sample source record.
    test_base = {
        "referenznummer": "TEST-001",
        "stellenangebotsTitel": "Data Analyst",
        "firma": "Example Employer",
        "stellenlokationen": [
            {
                "adresse": {
                    "ort": "Augsburg",
                }
            }
        ],
    }

    # This is a fictional ATS posting used only in tests.
    posting = (
        "https://jobs.lever.co/sample/"
        "12345678-1234-1234-1234-123456789abc"
    )

    # TEST 2 — Preserve external application URLs.
    with_link = normalize_job({
        **test_base,
        "externeUrl": posting,
    })

    assert (
        with_link["application_url"]
        == posting
    )

    assert (
        with_link["source_id"]
        == "TEST-001"
    )

    print(
        "PASS: External application URL preserved"
    )

    # TEST 3 — Alternative source field spelling.
    alternate = normalize_job({
        **test_base,
        "externeURL": posting,
    })

    assert (
        alternate["application_url"]
        == posting
    )

    print(
        "PASS: Alternative source URL spelling supported"
    )

    # TEST 4 — Missing external URLs remain unknown.
    without_link = normalize_job(
        test_base
    )

    assert (
        without_link["application_url"]
        is None
    )

    print(
        "PASS: Missing application URL stays unknown"
    )

    # TEST 5 — Preserve original publication date.
    dated = normalize_job({
        **test_base,
        "ersteVeroeffentlichungsdatum": "2024-02-16",
        "aktuelleVeroeffentlichungsdatum": "2026-10-20",
        "arbeitszeitmodelle": ["VOLLZEIT"],
    })

    assert (
        dated["original_published_at"]
        == "2024-02-16"
    )

    assert (
        dated["arbeitszeitmodelle"]
        == ["VOLLZEIT"]
    )

    print(
        "PASS: Original date and employment type preserved"
    )

    print(
        "\nPASS: All Bundesagentur adapter tests completed"
    )


# ============================================================
# STEP 9 — COMMAND-LINE INTERFACE
# ============================================================

def main() -> None:
    """
    Run tests, preview the search plan or fetch live jobs.

    --self-test:
        Run offline tests without API requests.

    --plan-only:
        Display the daily search plan without API requests.

    Without flags:
        Execute live Bundesagentur searches.
    """

    parser = argparse.ArgumentParser(
        description="JobRadar Bundesagentur Job Adapter"
    )

    parser.add_argument(
        "--self-test",
        action="store_true",
    )

    parser.add_argument(
        "--plan-only",
        action="store_true",
    )

    args = parser.parse_args()

    if args.self_test:

        run_self_test()
        return

    plan, count = build_search_plan()

    print(
        "JobRadar Germany — Dynamic Role Search"
    )

    print(
        "Configured role families:",
        count,
    )

    print(
        "Daily search terms:",
        len(plan),
    )

    for index, item in enumerate(plan, 1):

        print(
            f"{index}. {item['role']} -> "
            f"{item['query']} "
            f"({item['priority']})"
        )

    print(
        "Planned API requests:",
        len(plan) * len(SEARCH_LOCATIONS),
    )

    if args.plan_only:

        print(
            "PASS: Search plan generated; no API calls."
        )

        return

    print(
        "\nExecuting live searches..."
    )

    jobs = fetch_german_jobs()

    print(
        "\nPASS: Dynamic job search completed."
    )

    print(
        "Unique jobs collected:",
        len(jobs),
    )

    for index, job in enumerate(jobs[:10], 1):

        print(
            f"\n--- Job {index} ---"
        )

        print(
            "Title:",
            job["title"],
        )

        print(
            "Company:",
            job["company_name"],
        )

        print(
            "Location:",
            job["location"],
        )

        print(
            "Published:",
            job["published_at"],
        )

        print(
            "External application link:",
            job["application_url"],
        )

        print(
            "URL:",
            job["url"],
        )


if __name__ == "__main__":
    main()