"""
Day 11 — JobRadar Germany | Vacancy Quality Validator

THEORY
------
A production data pipeline needs a quality gate between job discovery
and AI scoring. Publication recency, source availability, and application
acceptance are different facts; one does not prove the others.

Rules:
- Reject clearly stale (>30 days), expired, closed, or unusable jobs.
- Hold undated, source-created-only, and not-yet-confirmed jobs for review.
- For Bundesagentur records, optionally re-fetch the source detail and check
  the original publication date (not merely a refreshed date).
- NEVER call an advertisement "confirmed open" merely because HTTP returns 200.

This module does not read or transmit any CV or personal information.
"""

import argparse
import base64
from datetime import date, datetime, timezone
from urllib.parse import quote, urlsplit

import requests


# Maximum permitted age of a job advertisement.
MAX_JOB_AGE_DAYS = 30

# Community-documented Bundesagentur job-detail endpoint.
BA_DETAILS_BASE = (
    "https://rest.arbeitsagentur.de/"
    "jobboerse/jobsuche-service/pc/v4/jobdetails/"
)

BA_HEADERS = {
    "X-API-Key": "jobboerse-jobsuche",
    "Accept": "application/json",
    "User-Agent": "JobRadarGermany/1.0",
}

# Known status values indicating a vacancy is closed.
CLOSED_STATES = {
    "closed",
    "expired",
    "inactive",
    "filled",
    "cancelled",
    "canceled",
    "archived",
    "withdrawn",
    "geschlossen",
    "abgelaufen",
    "besetzt",
    "zuruckgezogen",
    "not accepting applications",
}


def parse_date(value) -> date | None:
    """
    Convert a value into a Python date.

    Supports:
    - ISO dates
    - Python date/datetime objects
    - Unix timestamps in seconds or milliseconds

    Missing and invalid values return None.
    """

    if value is None or isinstance(value, bool):
        return None

    if isinstance(value, datetime):
        return value.date()

    if isinstance(value, date):
        return value

    if isinstance(value, (int, float)):
        try:
            timestamp = float(value)

            # Convert milliseconds into seconds.
            if abs(timestamp) > 100_000_000_000:
                timestamp /= 1000

            return datetime.fromtimestamp(
                timestamp,
                timezone.utc,
            ).date()

        except (ValueError, OverflowError, OSError):
            return None

    if isinstance(value, str):
        value = value.strip()

        if value.isdigit():
            return parse_date(int(value))

        try:
            return date.fromisoformat(value[:10])

        except ValueError:
            return None

    return None


def valid_https_url(value) -> bool:
    """
    Check whether a job URL uses HTTPS.

    IMPORTANT:
    A correctly formatted URL is not proof that
    the advertisement is still accepting applications.
    """

    if not isinstance(value, str):
        return False

    parts = urlsplit(value.strip())

    return (
        parts.scheme == "https"
        and bool(parts.hostname)
    )


def closed_signal(job: dict) -> str | None:
    """
    Look for explicit evidence that a vacancy is closed.

    We do not assume that a job is active simply because
    the source does not report a closure.
    """

    for field in ("is_active", "active"):

        if job.get(field) is False:
            return f"{field}=false"

    if job.get("is_closed") is True:
        return "is_closed=true"

    for field in (
        "status",
        "application_status",
        "vacancy_status",
    ):

        value = job.get(field)

        if (
            isinstance(value, str)
            and value.strip().casefold() in CLOSED_STATES
        ):
            return f"{field}={value}"

    return None


def assess_vacancy(
    job: dict,
    today: date | None = None,
) -> dict:
    """
    Evaluate a job without making network requests.

    Possible decisions:

    excluded:
        The vacancy violates a mandatory requirement.

    review:
        The vacancy may be suitable, but application
        availability has not been confirmed.

    IMPORTANT:
    This function never automatically returns
    'confirmed_open'.
    """

    today = today or date.today()

    reasons = []
    evidence = []

    # Reject malformed job records.
    if not isinstance(job, dict):

        return {
            "decision": "excluded",
            "reasons": ["Invalid job record"],
            "evidence": [],
            "age_days": None,
        }

    # CHECK 1 — Validate the application URL.
    url = job.get("url")

    if not valid_https_url(url):
        reasons.append(
            "Missing or invalid HTTPS job URL"
        )

    # CHECK 2 — Detect explicit closure signals.
    closed = closed_signal(job)

    if closed:
        reasons.append(
            f"Explicit closure signal: {closed}"
        )

    # CHECK 3 — Identify the most reliable available date.
    #
    # The original publication date takes priority.
    # A refreshed advertisement may actually be much older.
    original_date = (
        job.get("ersteVeroeffentlichungsdatum")
        or job.get("datumErsteVeroeffentlichung")
        or job.get("original_published_at")
    )

    published_date = job.get("published_at")
    created_date = job.get("created_at")

    date_basis = "original publication date"
    date_value = original_date

    if date_value is None:

        date_basis = "source publication date"
        date_value = published_date

    if date_value is None:

        date_basis = (
            "source creation date "
            "(not publication proof)"
        )

        date_value = created_date

    posted = parse_date(date_value)

    age_days = (
        (today - posted).days
        if posted
        else None
    )

    # CHECK 4 — Reject advertisements older than 30 days.
    if posted is None:

        evidence.append(
            "No trustworthy publication date found"
        )

    elif age_days < 0:

        reasons.append(
            f"Future-dated listing: {posted.isoformat()}"
        )

    else:

        evidence.append(
            f"{date_basis}: "
            f"{posted.isoformat()} "
            f"({age_days} days old)"
        )

        if age_days > MAX_JOB_AGE_DAYS:

            reasons.append(
                f"Older than {MAX_JOB_AGE_DAYS} days"
            )

    # CHECK 5 — Verify an explicitly provided deadline.
    deadline_raw = (
        job.get("application_deadline")
        or job.get("bewerbungsfrist")
        or job.get("valid_through")
        or job.get("expires_at")
    )

    deadline = parse_date(deadline_raw)

    if deadline is not None:

        evidence.append(
            f"Application deadline: {deadline.isoformat()}"
        )

        if today > deadline:

            reasons.append(
                "Application deadline has passed"
            )

    # CHECK 6 — Reject explicitly part-time-only positions.
    #
    # Your agreed employment preference is full-time.
    work_models = job.get("arbeitszeitmodelle")

    if isinstance(work_models, list):

        models = {
            str(value).upper()
            for value in work_models
        }

        if models and "VOLLZEIT" not in models:

            reasons.append(
                "Full-time employment not offered"
            )

    # If mandatory checks fail, exclude the vacancy.
    if reasons:

        return {
            "decision": "excluded",
            "reasons": reasons,
            "evidence": evidence,
            "age_days": age_days,
        }

    # Missing or uncertain publication information
    # requires additional verification.
    if posted is None:

        reasons.append(
            "Publication date must be verified"
        )

    elif (
        original_date is None
        and published_date is None
    ):

        reasons.append(
            "Creation date is not proof "
            "of publication date"
        )

    # We must independently confirm that applications
    # are being accepted before recommending the job.
    reasons.append(
        "Employer is still accepting applications: "
        "unconfirmed"
    )

    return {
        "decision": "review",
        "reasons": reasons,
        "evidence": evidence,
        "age_days": age_days,
    }


def fetch_ba_detail(
    reference: str,
    http_client=requests,
) -> dict | None:
    """
    Retrieve a Bundesagentur vacancy's detailed record.

    THEORY:
    A detail endpoint can provide additional source
    information missing from the initial search response.

    A 404 response indicates the detail is no longer
    retrievable through this endpoint.

    HTTP 200 does not prove the vacancy is open.
    """

    if (
        not isinstance(reference, str)
        or not reference.strip()
    ):

        raise ValueError(
            "Missing Bundesagentur reference ID"
        )

    # The documented endpoint uses a Base64 reference.
    encoded = base64.b64encode(
        reference.strip().encode("utf-8")
    ).decode("ascii")

    url = BA_DETAILS_BASE + quote(
        encoded,
        safe="",
    )

    response = http_client.get(
        url,
        headers=BA_HEADERS,
        timeout=20,
    )

    if response.status_code == 404:
        return None

    response.raise_for_status()

    details = response.json()

    if not isinstance(details, dict):

        raise ValueError(
            "Unexpected Bundesagentur job detail format"
        )

    # Verify the detail corresponds to the requested job.
    returned_reference = (
        details.get("referenznummer")
        or details.get("refnr")
    )

    if (
        returned_reference
        and str(returned_reference) != reference.strip()
    ):

        raise ValueError(
            "Source job reference does not match"
        )

    # A valid job-detail record should contain a title.
    if not (
        details.get("stellenangebotsTitel")
        or details.get("titel")
    ):

        raise ValueError(
            "Job detail has no title"
        )

    return details


def inspect_ba_vacancy(
    job: dict,
    http_client=requests,
    today: date | None = None,
) -> dict:
    """
    Recheck a Bundesagentur advertisement.

    Workflow:
    1. Apply the initial quality checks.
    2. Retrieve the detailed source record.
    3. Check the original publication date.
    4. Check closure and employment information.
    5. Return a conservative decision.

    An available detail is not automatically
    considered an active vacancy.
    """

    today = today or date.today()

    # Run initial checks before contacting the API.
    preliminary = assess_vacancy(
        job,
        today=today,
    )

    if preliminary["decision"] == "excluded":
        return preliminary

    reference = job.get("source_id")

    if not reference:

        preliminary["reasons"].append(
            "Cannot check details: no source ID"
        )

        return preliminary

    try:

        details = fetch_ba_detail(
            str(reference),
            http_client=http_client,
        )

    except (
        requests.RequestException,
        ValueError,
    ) as error:

        preliminary["reasons"].append(
            f"Job detail could not be verified: {error}"
        )

        return preliminary

    # The source no longer exposes the job detail.
    if details is None:

        return {
            "decision": "excluded",
            "reasons": [
                "Job detail is no longer retrievable "
                "from source"
            ],
            "evidence": preliminary["evidence"],
            "age_days": preliminary["age_days"],
        }

    # Copy original record to avoid modifying source data.
    enriched = job.copy()

    # Original publication date takes precedence
    # over newer search-result dates.
    original = details.get(
        "ersteVeroeffentlichungsdatum"
    )

    if original:

        enriched[
            "ersteVeroeffentlichungsdatum"
        ] = original

    # Copy additional quality-related details.
    for field in (
        "arbeitszeitmodelle",
        "is_active",
        "is_closed",
        "application_status",
        "status",
        "bewerbungsfrist",
    ):

        if field in details:
            enriched[field] = details[field]

    # Reassess using the enriched source information.
    assessment = assess_vacancy(
        enriched,
        today=today,
    )

    assessment["evidence"].append(
        "Bundesagentur detail retrieved at source"
    )

    if assessment["decision"] == "review":

        assessment["reasons"].append(
            "Application acceptance still requires "
            "employer/ATS confirmation"
        )

    return assessment


def run_self_test() -> None:
    """
    Test the vacancy-quality rules without Internet access.

    THEORY:
    Unit tests make it possible to verify business logic
    using controlled data instead of changing live results.

    Tests cover:
    - Recent advertisements
    - Stale advertisements
    - Closed positions
    - Expired deadlines
    - Unreliable publication dates
    - Part-time-only positions
    - Original dates older than refreshed dates
    """

    # Fixed test date makes results reproducible.
    today = date(2026, 10, 21)

    template = {
        "url": (
            "https://www.arbeitsagentur.de/"
            "jobsuche/jobdetail/TEST-001"
        ),
        "published_at": "2026-10-13",
        "source_id": "TEST-001",
    }

    def job(**fields):
        """Create a test vacancy with optional overrides."""

        return {
            **template,
            **fields,
        }

    # TEST 1 — A fresh advertisement is not
    # automatically confirmed open.
    assert (
        assess_vacancy(
            job(),
            today,
        )["decision"] == "review"
    )

    # TEST 2 — Reject an old advertisement.
    assert (
        assess_vacancy(
            job(published_at="2024-02-16"),
            today,
        )["decision"] == "excluded"
    )

    # TEST 3 — Reject explicitly closed vacancies.
    assert (
        assess_vacancy(
            job(status="closed"),
            today,
        )["decision"] == "excluded"
    )

    # TEST 4 — Reject expired application deadlines.
    assert (
        assess_vacancy(
            job(application_deadline="2026-10-20"),
            today,
        )["decision"] == "excluded"
    )

    # TEST 5 — Creation date alone is insufficient.
    assert (
        assess_vacancy(
            job(
                published_at=None,
                created_at="2026-10-20",
            ),
            today,
        )["decision"] == "review"
    )

    # TEST 6 — Reject part-time-only positions.
    assert (
        assess_vacancy(
            job(arbeitszeitmodelle=["TEILZEIT"]),
            today,
        )["decision"] == "excluded"
    )

    # TEST 7 — Simulate a source-detail response
    # containing an older original publication date.
    class FakeResponse:

        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):

            return {
                "referenznummer": "TEST-001",
                "stellenangebotsTitel": "Data Analyst",
                "ersteVeroeffentlichungsdatum": "2024-02-16",
            }

    class FakeClient:

        def get(self, *_args, **_kwargs):
            return FakeResponse()

    verified = inspect_ba_vacancy(
        job(),
        FakeClient(),
        today,
    )

    assert verified["decision"] == "excluded"

    print(
        "PASS: Fresh but unverified vacancies "
        "remain under review"
    )

    print("PASS: Old listings are excluded")

    print(
        "PASS: Closed listings and expired "
        "deadlines are excluded"
    )

    print(
        "PASS: Creation timestamps do not "
        "prove publication freshness"
    )

    print("PASS: Part-time-only jobs are excluded")

    print(
        "PASS: Old original publication dates "
        "override refreshed dates"
    )

    print(
        "PASS: All vacancy-quality tests completed"
    )


def main() -> None:
    """
    Command-line entry point.

    Supports:
    --self-test
        Run offline validation tests.

    --ba-reference
        Inspect an individual agency vacancy.

    --published-at
        Provide the listing's search-result date.
    """

    parser = argparse.ArgumentParser(
        description="JobRadar vacancy quality gate"
    )

    parser.add_argument(
        "--self-test",
        action="store_true",
    )

    parser.add_argument(
        "--ba-reference",
        default=None,
    )

    parser.add_argument(
        "--published-at",
        default=None,
    )

    args = parser.parse_args()

    if args.self_test:

        run_self_test()
        return

    if not args.ba_reference:

        parser.print_help()
        return

    reference = args.ba_reference.strip()

    job_record = {
        "source_id": reference,
        "source": "Bundesagentur für Arbeit",
        "url": (
            "https://www.arbeitsagentur.de/"
            "jobsuche/jobdetail/"
            + quote(reference, safe="")
        ),
        "published_at": args.published_at,
    }

    result = inspect_ba_vacancy(
        job_record
    )

    print(
        "Decision:",
        result["decision"].upper(),
    )

    print(
        "Age (days):",
        result["age_days"],
    )

    for reason in result["reasons"]:
        print("REASON:", reason)

    for evidence in result["evidence"]:
        print("EVIDENCE:", evidence)

    print(
        "NOTE: Review means NOT confirmed "
        "to be accepting applications."
    )


if __name__ == "__main__":
    main()