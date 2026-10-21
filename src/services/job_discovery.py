"""
Day 11 — JobRadar Germany
Module: Quality-Gated Multi-Source Discovery

THEORY
------
A data pipeline should separate evidence from recommendations.

1. Source APIs provide job advertisements, not guarantees of vacancy status.
2. Deterministic filters eliminate stale, closed, foreign, and student jobs.
3. Employer application links must come from an actual source record.
4. An ATS API can establish that an exact job is publicly listed today.
5. 'ATS live' is NOT proof that every other job requirement is met.

This module NEVER labels a listing 'confirmed open' merely because an
ATS returned it. Full application acceptance and suitability require
additional checks. No personal CV data is used by this module.
"""

import argparse
from datetime import date
from urllib.parse import urlsplit, urlunsplit

import requests

from src.sources.arbeitnow import fetch_jobs
from src.sources.arbeitsagentur import fetch_german_jobs

from src.services.application_links import (
    discover_application_link,
    verify_job_application,
)

from src.services.geography import classify_geography
from src.services.job_filter import load_roles, match_job_title
from src.services.location_filter import load_location_settings
from src.services.seniority_filter import classify_seniority

from src.services.vacancy_validator import (
    assess_vacancy,
    inspect_ba_vacancy,
    parse_date,
)


BA_SOURCE = "Bundesagentur für Arbeit"


# ============================================================
# STEP 1 — NORMALIZE JOB URLS
# ============================================================

def canonical_url(url: str) -> str | None:
    """
    Normalize an HTTPS job URL for duplicate detection.

    This finds repeated URLs, not necessarily identical
    vacancies published on different websites.
    """

    if not isinstance(url, str):
        return None

    parts = urlsplit(url.strip())

    if parts.scheme != "https" or not parts.hostname:
        return None

    return urlunsplit((
        "https",
        parts.netloc.lower(),
        parts.path.rstrip("/") or "/",
        parts.query,
        "",
    ))


# ============================================================
# STEP 2 — COLLECT JOBS FROM BOTH SOURCES
# ============================================================

def collect_sources() -> list[dict]:
    """
    Collect independent job sources.

    If one source becomes unavailable, the other
    source can still provide vacancies.
    """

    collected = []

    sources = (
        ("Arbeitnow", fetch_jobs),
        (BA_SOURCE, fetch_german_jobs),
    )

    for name, fetcher in sources:

        print(f"\nConnecting to {name}...")

        try:
            jobs = fetcher()

            if not isinstance(jobs, list):
                raise ValueError(
                    "Source returned a non-list response"
                )

        except (
            requests.RequestException,
            ValueError,
            RuntimeError,
        ) as error:

            print(f"WARNING: {name}: {error}")
            continue

        print("Records collected:", len(jobs))

        for item in jobs:

            if isinstance(item, dict):

                # Copy the source record to avoid mutation.
                record = item.copy()

                record.setdefault("source", name)

                collected.append(record)

    if not collected:
        raise RuntimeError(
            "Neither source returned job records"
        )

    return collected


# ============================================================
# STEP 3 — VERIFY FULL-TIME EMPLOYMENT
# ============================================================

def full_time_status(
    job: dict,
    ats_result: dict | None,
) -> str:
    """
    Evaluate explicit employment-type information.

    Possible results:
        confirmed
        not_full_time
        conflicting
        unknown

    THEORY:
    Missing information is NOT evidence of full-time work.

    We use:
    - Bundesagentur employment-type information
    - Employer ATS employment-type information

    If two sources disagree, we require review.
    """

    source_models = job.get("arbeitszeitmodelle")

    source_state = None

    if (
        isinstance(source_models, list)
        and source_models
    ):

        normalized = {
            str(model)
            .casefold()
            .replace(" ", "")
            .replace("-", "")
            for model in source_models
        }

        if normalized.intersection({
            "vollzeit",
            "fulltime",
        }):

            source_state = "full"

        else:

            source_state = "other"

    ats_state = None

    if isinstance(ats_result, dict):

        employment = ats_result.get(
            "employment_type"
        )

        if (
            isinstance(employment, str)
            and employment.strip()
        ):

            value = (
                employment
                .casefold()
                .replace(" ", "")
                .replace("-", "")
            )

            if value in {
                "fulltime",
                "vollzeit",
            }:

                ats_state = "full"

            elif value in {
                "parttime",
                "teilzeit",
                "internship",
                "workingstudent",
                "werkstudent",
                "contract",
                "freelance",
                "temporary",
            }:

                ats_state = "other"

    states = {
        state
        for state in (source_state, ats_state)
        if state
    }

    if not states:
        return "unknown"

    if states == {"full"}:
        return "confirmed"

    if states == {"other"}:
        return "not_full_time"

    return "conflicting"


# ============================================================
# STEP 4 — APPLY THE COMPLETE DISCOVERY PIPELINE
# ============================================================

def prepare_jobs(
    raw_jobs: list[dict],
    *,
    today: date | None = None,
    verify_ba_details: int = 0,
    verify_ats_limit: int = 0,
    ats_checker=None,
) -> dict:
    """
    Filter and enrich vacancies conservatively.

    THE PROCESS:
    1. Validate and deduplicate jobs.
    2. Match configured role families.
    3. Check geography and seniority.
    4. Apply the 30-day vacancy-quality gate.
    5. Discover genuine employer ATS URLs.
    6. Optionally check current employer ATS postings.
    7. Evaluate full-time employment.
    8. Separate ATS-live and unverified candidates.

    NETWORK RULES:
    No extra ATS or BA detail requests occur by default.

    The limits can be increased explicitly during testing.

    Result groups:
    - ats_live_candidates
    - verification_queue
    - excluded_stale_or_closed
    - excluded_foreign
    - excluded_seniority
    - verified_open

    IMPORTANT:
    verified_open remains empty until final application
    acceptance checks are implemented.
    """

    if (
        verify_ba_details < 0
        or verify_ats_limit < 0
    ):

        raise ValueError(
            "Verification limits cannot be negative"
        )

    today = today or date.today()

    roles = load_roles()

    locations = load_location_settings()

    # A test can inject a fake verifier so no Internet
    # connection is needed.
    checker = ats_checker or verify_job_application

    output = {
        "verified_open": [],
        "ats_live_candidates": [],
        "verification_queue": [],
        "excluded_stale_or_closed": [],
        "excluded_foreign": [],
        "excluded_seniority": [],
        "title_matches": 0,
        "duplicate_urls": 0,
        "employer_links_found": 0,
        "ats_checks": 0,
        "ba_details_checked": 0,
    }

    seen_urls = set()

    for source_job in raw_jobs:

        if not isinstance(source_job, dict):
            continue

        title = str(
            source_job.get("title") or ""
        ).strip()

        url = canonical_url(
            source_job.get("url")
        )

        # ----------------------------------------------------
        # CHECK 1 — VALID RECORDS AND DUPLICATES
        # ----------------------------------------------------

        if not title or not url:
            continue

        if url in seen_urls:

            output["duplicate_urls"] += 1
            continue

        seen_urls.add(url)

        # ----------------------------------------------------
        # CHECK 2 — TARGET JOB ROLES
        # ----------------------------------------------------

        matched = match_job_title(
            title,
            roles,
        )

        if matched is None:
            continue

        output["title_matches"] += 1

        # ----------------------------------------------------
        # CHECK 3 — GEOGRAPHY AND SENIORITY
        # ----------------------------------------------------

        geography = classify_geography(
            source_job,
            locations,
        )

        seniority, seniority_priority = (
            classify_seniority(title)
        )

        record = {
            "title": title,

            "company": (
                source_job.get("company_name")
                or "Unknown"
            ),

            "location": str(
                source_job.get("location")
                or "Unknown"
            ),

            "url": url,

            "source": str(
                source_job.get("source")
                or "Unknown"
            ),

            "source_id": source_job.get("source_id"),

            "role_priority": matched["priority"],

            "matched_role": matched["matched_role"],

            "seniority": seniority,

            "geography": geography,

            "employer_ats_url": None,

            "employer_link_status": "not_checked",

            "ats_status": "not_checked",

            "full_time_status": "unknown",

            "age_days": None,

            "review_reasons": [],

            "quality_evidence": [],
        }

        # Exclude student and internship positions.
        if seniority_priority == "excluded":

            record["review_reasons"].append(
                "Student/internship role"
            )

            output["excluded_seniority"].append(
                record
            )

            continue

        # Exclude clearly foreign on-site jobs.
        if geography == "outside_germany":

            record["review_reasons"].append(
                "On-site job outside Germany"
            )

            output["excluded_foreign"].append(
                record
            )

            continue

        # ----------------------------------------------------
        # CHECK 4 — VACANCY FRESHNESS AND CLOSURE
        # ----------------------------------------------------

        quality = assess_vacancy(
            source_job,
            today=today,
        )

        if quality["decision"] == "excluded":

            record["age_days"] = quality["age_days"]

            record["review_reasons"] = list(
                quality["reasons"]
            )

            output["excluded_stale_or_closed"].append(
                record
            )

            continue

        # Optional BA detail verification.
        # An older original publication date may be found.
        if (
            record["source"] == BA_SOURCE
            and source_job.get("source_id")
            and output["ba_details_checked"]
            < verify_ba_details
        ):

            output["ba_details_checked"] += 1

            quality = inspect_ba_vacancy(
                source_job,
                today=today,
            )

            if quality["decision"] == "excluded":

                record["age_days"] = quality["age_days"]

                record["review_reasons"] = list(
                    quality["reasons"]
                )

                output["excluded_stale_or_closed"].append(
                    record
                )

                continue

        record["age_days"] = quality["age_days"]

        record["quality_evidence"] = list(
            quality["evidence"]
        )

        record["review_reasons"] = list(
            quality["reasons"]
        )

        # ----------------------------------------------------
        # CHECK 5 — DISCOVER EMPLOYER APPLICATION LINKS
        # ----------------------------------------------------

        # We supply the original source record because
        # it may contain application_url or description.
        link = discover_application_link(
            source_job
        )

        record["employer_link_status"] = (
            link["status"]
        )

        record["employer_ats_url"] = (
            link["ats_url"]
        )

        if link["status"] == "found":

            output["employer_links_found"] += 1

        # ----------------------------------------------------
        # CHECK 6 — OPTIONAL EMPLOYER ATS VERIFICATION
        # ----------------------------------------------------

        ats = None

        if (
            link["status"] == "found"
            and output["ats_checks"] < verify_ats_limit
        ):

            output["ats_checks"] += 1

            ats = checker(source_job)

            record["ats_status"] = ats.get(
                "ats_status",
                "review",
            )

            # Exact posting not listed on the employer ATS.
            if record["ats_status"] == "not_listed":

                record["review_reasons"].append(
                    "Exact posting no longer listed "
                    "by employer ATS"
                )

                output["excluded_stale_or_closed"].append(
                    record
                )

                continue

            # If the ATS provides an older publication
            # date, it overrides apparent freshness.
            ats_date = parse_date(
                ats.get("published_at")
            )

            if ats_date is not None:

                ats_age = (
                    today - ats_date
                ).days

                if (
                    ats_age < 0
                    or ats_age > 30
                ):

                    record["review_reasons"].append(
                        "ATS publication date outside "
                        f"30-day window: {ats_date}"
                    )

                    output["excluded_stale_or_closed"].append(
                        record
                    )

                    continue

        # ----------------------------------------------------
        # CHECK 7 — FULL-TIME EMPLOYMENT
        # ----------------------------------------------------

        record["full_time_status"] = (
            full_time_status(
                source_job,
                ats,
            )
        )

        if record["full_time_status"] != "confirmed":

            record["review_reasons"].append(
                "Full-time employment is not "
                "reliably confirmed"
            )

        # ----------------------------------------------------
        # CHECK 8 — GERMANY AND RELOCATION ELIGIBILITY
        # ----------------------------------------------------

        # Local and explicitly Germany-eligible remote roles
        # are preferred. Other areas require investigation.
        if geography not in {
            "preferred_city",
            "nearby_area",
            "remote_germany",
        }:

            record["review_reasons"].append(
                "Check Germany eligibility, commute, "
                "or relocation match"
            )

        # ----------------------------------------------------
        # CHECK 9 — TRUSTWORTHY PUBLICATION DATE
        # ----------------------------------------------------

        # A source creation timestamp alone cannot
        # establish a recent publication date.
        if not (
            source_job.get("original_published_at")
            or source_job.get("published_at")
            or (
                ats
                and ats.get("published_at")
            )
        ):

            record["review_reasons"].append(
                "No independently reliable "
                "publication date"
            )

        # ----------------------------------------------------
        # CHECK 10 — FINAL GROUPING
        # ----------------------------------------------------

        if (
            ats
            and record["ats_status"] == "live_on_ats"
        ):

            # The employer currently publishes the posting.
            # That does not automatically establish final
            # application acceptance or suitability.
            record["review_reasons"].append(
                "ATS posting published; final "
                "application acceptance unconfirmed"
            )

            output["ats_live_candidates"].append(
                record
            )

        else:

            if record["employer_link_status"] != "found":

                record["review_reasons"].append(
                    "Direct employer ATS posting "
                    "link unavailable"
                )

            elif record["ats_status"] == "not_checked":

                record["review_reasons"].append(
                    "Employer ATS has not yet been checked"
                )

            else:

                record["review_reasons"].append(
                    "ATS response needs manual verification"
                )

            output["verification_queue"].append(
                record
            )

    # --------------------------------------------------------
    # SORT BY ROLE PRIORITY
    # --------------------------------------------------------

    priority_order = {
        "priority_a": 0,
        "priority_b": 1,
        "priority_c": 2,
    }

    for group in (
        "ats_live_candidates",
        "verification_queue",
    ):

        output[group].sort(
            key=lambda job: priority_order.get(
                job["role_priority"],
                99,
            )
        )

    return output


# ============================================================
# STEP 5 — DISPLAY RESULT PREVIEWS
# ============================================================

def show_jobs(
    records: list[dict],
    limit: int = 8,
) -> None:
    """Display jobs and their outstanding verification issues."""

    for index, job in enumerate(
        records[:limit],
        start=1,
    ):

        print(f"\n--- Candidate {index} ---")

        print("Title:", job["title"])

        print("Company:", job["company"])

        print("Location:", job["location"])

        print("Source:", job["source"])

        print(
            "Employer ATS link:",
            job["employer_ats_url"],
        )

        print(
            "ATS status:",
            job["ats_status"],
        )

        print(
            "Full-time status:",
            job["full_time_status"],
        )

        print(
            "Review:",
            "; ".join(job["review_reasons"]),
        )

        print("Source URL:", job["url"])


# ============================================================
# STEP 6 — OFFLINE INTEGRATION TESTS
# ============================================================

def run_self_test() -> None:
    """
    Test the complete pipeline without contacting APIs.

    THEORY:
    Dependency injection lets us replace a live ATS verifier
    with a predictable fake function during testing.

    This validates the pipeline while avoiding real network
    requests and changing external job data.
    """

    today = date(2026, 10, 21)

    # Fictional ATS posting used only during testing.
    lever = (
        "https://jobs.lever.co/examplecompany/"
        "12345678-1234-1234-1234-123456789abc"
    )

    def sample(
        identifier: str,
        **changes,
    ) -> dict:
        """Create a fictional vacancy for offline tests."""

        job = {
            "title": "Data Analyst",

            "company_name": "Example Employer",

            "location": "Augsburg",

            "url": (
                "https://example.org/jobs/"
                + identifier
            ),

            "source": BA_SOURCE,

            "source_id": identifier,

            "published_at": "2026-10-15",

            "remote": False,
        }

        job.update(changes)

        return job

    # Eight fictional test advertisements.
    jobs = [
        # Valid recent job with direct ATS evidence.
        sample(
            "fresh",
            application_url=lever,
            original_published_at="2026-10-15",
            arbeitszeitmodelle=["VOLLZEIT"],
        ),

        # Posting no longer published on employer ATS.
        sample(
            "not-listed",
            application_url=lever,
            arbeitszeitmodelle=["VOLLZEIT"],
        ),

        # Stale advertisement.
        sample(
            "old",
            published_at="2024-02-16",
        ),

        # Explicit closure.
        sample(
            "closed",
            status="closed",
        ),

        # Foreign on-site job.
        sample(
            "foreign",
            location="Zurich, Switzerland",
        ),

        # Student position.
        sample(
            "student",
            title="Working Student Data Analyst",
        ),

        # Unknown publication date.
        sample(
            "undated",
            published_at=None,
        ),

        # Germany-wide remote job.
        sample(
            "remote",
            location="Remote - Germany",
            remote=True,
        ),
    ]

    calls = []

    def fake_ats_checker(job: dict) -> dict:
        """
        Simulate employer ATS responses.

        No real Internet access is used.
        """

        calls.append(
            job["source_id"]
        )

        return {
            "ats_status": (
                "not_listed"
                if job["source_id"] == "not-listed"
                else "live_on_ats"
            ),

            "employment_type": "Full-time",

            "published_at": "2026-10-15",
        }

    # Execute the actual filtering code against test data.
    results = prepare_jobs(
        jobs,
        today=today,
        verify_ats_limit=2,
        ats_checker=fake_ats_checker,
    )

    # All eight test job titles match our Data Analyst role.
    assert results["title_matches"] == 8

    # One posting is live on its fictional ATS.
    assert len(
        results["ats_live_candidates"]
    ) == 1

    # Two records still require verification.
    assert len(
        results["verification_queue"]
    ) == 2

    # Stale, closed and no-longer-listed vacancies.
    assert len(
        results["excluded_stale_or_closed"]
    ) == 3

    # Foreign on-site job.
    assert len(
        results["excluded_foreign"]
    ) == 1

    # Working Student role.
    assert len(
        results["excluded_seniority"]
    ) == 1

    # No vacancy is automatically labeled confirmed open.
    assert len(
        results["verified_open"]
    ) == 0

    # Exactly two ATS checks were performed.
    assert results["ats_checks"] == 2

    assert set(calls) == {
        "fresh",
        "not-listed",
    }

    print(
        "PASS: Genuine employer ATS links reach the verifier"
    )

    print(
        "PASS: ATS missing postings, old and closed jobs excluded"
    )

    print(
        "PASS: Foreign and student positions excluded"
    )

    print(
        "PASS: Unsupported/unverified jobs stay in the review queue"
    )

    print(
        "PASS: ATS-live jobs are not labeled confirmed open"
    )

    print(
        "PASS: All offline integration tests completed"
    )


# ============================================================
# STEP 7 — COMMAND-LINE INTERFACE
# ============================================================

def main() -> None:
    """
    Run tests or discover jobs with bounded verification.

    --self-test:
        Run offline integration tests.

    --verify-ats N:
        Check at most N supported ATS postings.

    --verify-ba-details N:
        Check at most N Bundesagentur detail records.

    Both network-verification limits default to zero.
    """

    parser = argparse.ArgumentParser(
        description="JobRadar source-to-ATS discovery"
    )

    parser.add_argument(
        "--self-test",
        action="store_true",
    )

    parser.add_argument(
        "--verify-ats",
        type=int,
        default=0,
        metavar="N",
    )

    parser.add_argument(
        "--verify-ba-details",
        type=int,
        default=0,
        metavar="N",
    )

    args = parser.parse_args()

    if args.self_test:

        run_self_test()
        return

    try:

        collected = collect_sources()

        found = prepare_jobs(
            collected,
            verify_ats_limit=args.verify_ats,
            verify_ba_details=args.verify_ba_details,
        )

    except (
        RuntimeError,
        ValueError,
        KeyError,
    ) as error:

        print("ERROR:", error)

        raise SystemExit(1) from error

    # --------------------------------------------------------
    # PRINT DISCOVERY SUMMARY
    # --------------------------------------------------------

    print(
        "\n========== DISCOVERY SUMMARY =========="
    )

    print(
        "Source records:",
        len(collected),
    )

    print(
        "Relevant title matches:",
        found["title_matches"],
    )

    print(
        "Duplicate URLs:",
        found["duplicate_urls"],
    )

    print(
        "Excluded: stale/closed/invalid:",
        len(found["excluded_stale_or_closed"]),
    )

    print(
        "Excluded: foreign on-site:",
        len(found["excluded_foreign"]),
    )

    print(
        "Excluded: student/internship:",
        len(found["excluded_seniority"]),
    )

    print(
        "Direct employer ATS links found:",
        found["employer_links_found"],
    )

    print(
        "ATS checks performed:",
        found["ats_checks"],
    )

    print(
        "BA detail checks performed:",
        found["ba_details_checked"],
    )

    print(
        "ATS-live candidates (not recommendations):",
        len(found["ats_live_candidates"]),
    )

    print(
        "Awaiting verification:",
        len(found["verification_queue"]),
    )

    print(
        "CONFIRMED OPEN:",
        len(found["verified_open"]),
    )

    # --------------------------------------------------------
    # DISPLAY CANDIDATES
    # --------------------------------------------------------

    print(
        "\n========== ATS-LIVE CANDIDATES =========="
    )

    show_jobs(
        found["ats_live_candidates"]
    )

    print(
        "\n========== VERIFICATION QUEUE =========="
    )

    show_jobs(
        found["verification_queue"]
    )

    print(
        "\nIMPORTANT: No job is recommended until "
        "application acceptance,"
    )

    print(
        "freshness, full-time status and Germany "
        "eligibility are confirmed."
    )


if __name__ == "__main__":
    main()