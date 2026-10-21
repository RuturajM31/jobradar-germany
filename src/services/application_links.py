"""
Day 11 — JobRadar Germany
Module: Employer Application Link Discovery

THEORY
------
A job aggregator and the employer's recruitment system
are not equivalent sources of vacancy status.

This module searches for a DIRECT, explicitly supplied
ATS posting link in a job-source record.

It NEVER guesses an employer's recruitment URL.

Three verification stages:
1. Find a genuine employer ATS job URL.
2. Verify the exact posting through the ATS API.
3. Apply our separate 30-day freshness, full-time,
   location and language requirements.

Supported ATS platforms:
- Greenhouse
- Lever
- Ashby

PRIVACY:
No CV or personal information is accessed or transmitted.

The link-discovery process works without Internet access.
"""

from html.parser import HTMLParser
from urllib.parse import urlsplit, urlunsplit

import requests

from src.services.employer_verifier import (
    ats_identity,
    verify_employer_posting,
)


# ============================================================
# CONFIGURATION
# ============================================================

# Explicit application URLs from APIs are preferable
# to hyperlinks found inside job descriptions.

DIRECT_LINK_FIELDS = (
    "application_url",
    "apply_url",
    "applyUrl",
    "externeUrl",
    "external_url",
    "employer_application_url",
    "jobUrl",
    "job_url",
)


# ============================================================
# THEORY: HTML PARSING
# ============================================================

class AnchorCollector(HTMLParser):
    """
    Extract hyperlinks from HTML content.

    For example:

        <a href="https://jobs.lever.co/...">
            Apply here
        </a>

    The href attribute contains the destination URL.

    IMPORTANT:
    Finding a hyperlink is not proof that the associated
    vacancy is still accepting applications.
    """

    def __init__(self):

        super().__init__(convert_charrefs=True)

        self.links = []

    def handle_starttag(self, tag, attrs):
        """Collect the href values of HTML anchor elements."""

        if tag.casefold() != "a":
            return

        for name, value in attrs:

            if (
                name.casefold() == "href"
                and isinstance(value, str)
            ):

                self.links.append(value)


# ============================================================
# STEP 1 — VALIDATE EMPLOYER ATS URLS
# ============================================================

def canonical_ats_posting(value: str) -> str | None:
    """
    Accept only recognized direct ATS job-posting URLs.

    Supported URL formats include:

    Greenhouse:
        https://job-boards.greenhouse.io/company/jobs/123456

    Lever:
        https://jobs.lever.co/company/job-id

    Ashby:
        https://jobs.ashbyhq.com/company/job-id

    For Lever and Ashby, application-link suffixes are
    removed to recover the original posting URL.

    SECURITY:
    - Only HTTPS links are accepted.
    - The host must belong to a supported ATS.
    - Arbitrary employer websites are never requested.
    - Employer names and job IDs are never invented.
    """

    if not isinstance(value, str):
        return None

    parsed = urlsplit(value.strip())

    if (
        parsed.scheme != "https"
        or not parsed.hostname
    ):
        return None

    # Reject URLs containing embedded credentials
    # or nonstandard port overrides.

    try:

        if (
            parsed.username
            or parsed.password
            or parsed.port is not None
        ):
            return None

    except ValueError:
        return None

    # Remove trailing slash, tracking query parameters,
    # and fragments.

    path = parsed.path.rstrip("/")

    # Lever often uses /apply after the posting ID.
    if (
        parsed.hostname in (
            "jobs.lever.co",
            "jobs.eu.lever.co",
        )
        and path.endswith("/apply")
    ):

        path = path[:-len("/apply")]

    # Ashby may use /application after the posting ID.
    if (
        parsed.hostname == "jobs.ashbyhq.com"
        and path.endswith("/application")
    ):

        path = path[:-len("/application")]

    candidate = urlunsplit((
        "https",
        parsed.hostname.lower(),
        path,
        "",
        "",
    ))

    # Reuse our previously tested ATS URL validator.
    if ats_identity(candidate) is None:
        return None

    return candidate


# ============================================================
# STEP 2 — REMOVE DUPLICATE EMPLOYER LINKS
# ============================================================

def distinct_ats_urls(values: list[str]) -> list[str]:
    """
    Return unique, recognized ATS posting URLs.

    A description may contain the same application
    link multiple times.

    Deduplication prevents unnecessary ATS requests.
    """

    result = []

    seen = set()

    for value in values:

        candidate = canonical_ats_posting(value)

        if (
            candidate
            and candidate not in seen
        ):

            seen.add(candidate)

            result.append(candidate)

    return result


# ============================================================
# STEP 3 — EXTRACT LINKS FROM JOB DESCRIPTIONS
# ============================================================

def extract_description_links(
    description: str,
) -> list[str]:
    """
    Extract recognized ATS links from an HTML description.

    Example:
    An Arbeitnow listing may contain a link pointing
    directly to an employer's recruitment platform.

    We only accept supported ATS posting URLs.

    Generic websites and unrelated links are ignored.
    """

    if not isinstance(description, str):
        return []

    collector = AnchorCollector()

    collector.feed(description)

    return distinct_ats_urls(
        collector.links
    )


# ============================================================
# STEP 4 — DISCOVER THE APPLICATION LINK
# ============================================================

def discover_application_link(job: dict) -> dict:
    """
    Find an unambiguous ATS job-posting URL.

    Search priority:

    1. Existing direct ATS job URL
    2. Structured external/application URL fields
    3. Links inside the HTML job description

    Returns:

        found:
            Exactly one direct ATS posting was found.

        missing:
            No supported direct ATS link was found.

        ambiguous:
            Multiple different ATS postings were found
            at the same evidence level.

    IMPORTANT:
    If several possible vacancies are detected,
    JobRadar will not select one arbitrarily.
    """

    if not isinstance(job, dict):

        return {
            "status": "missing",
            "ats_url": None,
            "provider": None,
            "origin": None,
            "explanation": "Invalid source record.",
        }

    groups = [
        (
            "source_url",
            [job.get("url")],
        ),
        (
            "structured_field",
            [
                job.get(field)
                for field in DIRECT_LINK_FIELDS
            ],
        ),
        (
            "description",
            extract_description_links(
                job.get("description")
            ),
        ),
    ]

    for origin, values in groups:

        candidates = distinct_ats_urls([
            value
            for value in values
            if isinstance(value, str)
        ])

        # More than one posting means uncertainty.
        if len(candidates) > 1:

            return {
                "status": "ambiguous",
                "ats_url": None,
                "provider": None,
                "origin": origin,
                "explanation": (
                    "Multiple direct ATS postings found; "
                    "manual review is required."
                ),
            }

        # Exactly one supported ATS posting.
        if len(candidates) == 1:

            candidate = candidates[0]

            provider = ats_identity(
                candidate
            )[0]

            return {
                "status": "found",
                "ats_url": candidate,
                "provider": provider,
                "origin": origin,
                "explanation": (
                    "Explicit direct ATS posting link found."
                ),
            }

    # Never guess an employer-specific posting URL.
    return {
        "status": "missing",
        "ats_url": None,
        "provider": None,
        "origin": None,
        "explanation": (
            "No supported employer ATS URL was "
            "explicitly provided by this source."
        ),
    }


# ============================================================
# STEP 5 — CONNECT TO OUR EMPLOYER VERIFIER
# ============================================================

def verify_job_application(
    job: dict,
    *,
    http_client=requests,
) -> dict:
    """
    Find the original application link and verify it.

    Workflow:

    1. Look for a direct ATS posting URL.
    2. Identify Greenhouse, Lever or Ashby.
    3. Check the exact vacancy through the public ATS API.
    4. Return the verification result.

    IMPORTANT:
    A result of 'live_on_ats' means the posting is
    currently published by that recruitment platform.

    It does NOT automatically mean the job qualifies
    for our final recommendations.

    The separate quality gate must still establish:
    - Publication within the last 30 days
    - Full-time employment
    - German work eligibility
    - Appropriate work arrangement
    - Other job requirements
    """

    discovered = discover_application_link(
        job
    )

    # Do not make network requests when no
    # trustworthy ATS link was found.

    if discovered["status"] != "found":

        return {
            **discovered,
            "ats_status": "review",
            "application_url": None,
            "employment_type": None,
            "published_at": None,
        }

    # Reuse the ATS verifier built in Step 34.

    checked = verify_employer_posting(
        discovered["ats_url"],
        expected_title=job.get("title"),
        http_client=http_client,
    )

    return {
        **discovered,
        "ats_status": checked["status"],
        "explanation": checked["explanation"],
        "application_url": checked["application_url"],
        "employment_type": checked["employment_type"],
        "published_at": checked["published_at"],
    }


# ============================================================
# STEP 6 — OFFLINE UNIT TESTS
# ============================================================

def run_self_test() -> None:
    """
    Test application-link discovery without Internet access.

    THEORY:
    Unit tests verify data processing with predictable inputs.

    Tests cover:
    1. Structured Bundesagentur external URL
    2. ATS link inside HTML
    3. Direct Greenhouse posting
    4. Generic employer websites
    5. Multiple ambiguous ATS links
    6. Insecure and unsupported URLs
    """

    # Example direct ATS posting URLs.
    # These are TEST identifiers, not real vacancies.

    greenhouse = (
        "https://job-boards.greenhouse.io/"
        "examplecompany/jobs/123456"
    )

    lever = (
        "https://jobs.lever.co/examplecompany/"
        "12345678-1234-1234-1234-123456789abc"
    )

    ashby = (
        "https://jobs.ashbyhq.com/examplecompany/"
        "12345678-1234-1234-1234-123456789abc"
    )

    # --------------------------------------------------------
    # TEST 1 — Bundesagentur external application URL
    # --------------------------------------------------------

    source = {
        "title": "Data Analyst",
        "url": (
            "https://www.arbeitsagentur.de/"
            "jobsuche/jobdetail/X"
        ),
        "externeUrl": lever + "/apply",
    }

    found = discover_application_link(
        source
    )

    assert found["status"] == "found"

    assert found["ats_url"] == lever

    print(
        "PASS: BA external application URL identified"
    )

    # --------------------------------------------------------
    # TEST 2 — ATS link inside an HTML description
    # --------------------------------------------------------

    html_job = {
        "url": (
            "https://www.arbeitnow.com/jobs/example"
        ),
        "description": (
            '<p>Apply:</p><a href="'
            + ashby
            + '">Apply here</a>'
        ),
    }

    found = discover_application_link(
        html_job
    )

    assert found["status"] == "found"

    assert found["provider"] == "ashby"

    print(
        "PASS: ATS URL extracted from HTML description"
    )

    # --------------------------------------------------------
    # TEST 3 — Direct ATS job URL
    # --------------------------------------------------------

    direct = discover_application_link({
        "url": greenhouse,
    })

    assert direct["ats_url"] == greenhouse

    print(
        "PASS: Direct ATS job URL accepted"
    )

    # --------------------------------------------------------
    # TEST 4 — Generic careers website
    # --------------------------------------------------------

    missing = discover_application_link({
        "url": (
            "https://www.arbeitnow.com/jobs/sample"
        ),
        "company_name": "Example Company",
        "description": (
            '<a href="https://example.com">'
            "Careers</a>"
        ),
    })

    assert missing["status"] == "missing"

    print(
        "PASS: Generic careers page not mistaken for ATS"
    )

    # --------------------------------------------------------
    # TEST 5 — Multiple different ATS job links
    # --------------------------------------------------------

    ambiguous = discover_application_link({
        "description": (
            f'<a href="{greenhouse}">Job A</a>'
            f'<a href="{ashby}">Job B</a>'
        ),
    })

    assert ambiguous["status"] == "ambiguous"

    print(
        "PASS: Multiple ATS links flagged as ambiguous"
    )

    # --------------------------------------------------------
    # TEST 6 — Reject unsupported or insecure URLs
    # --------------------------------------------------------

    rejected = distinct_ats_urls([
        (
            "http://jobs.lever.co/"
            "examplecompany/not-secure"
        ),
        (
            "https://example.com/jobs/data-analyst"
        ),
    ])

    assert rejected == []

    print(
        "PASS: Unsupported and insecure URLs rejected"
    )

    print(
        "\nPASS: All application-link discovery tests completed"
    )


# ============================================================
# PROGRAM ENTRY POINT
# ============================================================

if __name__ == "__main__":
    run_self_test()