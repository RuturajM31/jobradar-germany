"""
Day 11 — JobRadar Germany
Module: Real Job Data for CrewAI

THEORY
------
A deterministic data pipeline decides job status.

An LLM may explain and summarize evidence, but it cannot
verify vacancies or override Python's quality decisions.

This module connects:
    Real job APIs
        -> Job discovery and filters
        -> Privacy-safe data preparation
        -> Optional CrewAI execution

PRIVACY
-------
Only allowlisted PUBLIC job metadata may reach Ollama Cloud.

Never transmit:
- Complete CV
- Private professional profile
- Contact information
- Raw job descriptions
- Personal identifiers
"""

import argparse
import json
import os

from src.services.job_discovery import (
    collect_sources,
    prepare_jobs,
)

from src.crew.workflow import (
    build_jobradar_crew,
    prepare_crew_inputs,
)

from src.crew.llm_config import build_ollama_llm


# ============================================================
# CONFIGURATION
# ============================================================

MARKER = "NO_CONFIRMED_OPEN_JOBS"

# Limit the number of public leads provided to the AI.
MAX_LEADS = 8


# ============================================================
# STEP 1 — PREPARE REAL JOB INPUTS
# ============================================================

def build_inputs(
    results: dict,
) -> dict[str, str]:
    """
    Convert discovery results into safe CrewAI inputs.

    THEORY:
    The existing workflow uses an allowlist to ensure
    that unnecessary or private information is removed.

    Only unverified public job metadata is included.

    The confirmed-open list must remain empty until
    our final employer eligibility verification exists.
    """

    inputs = prepare_crew_inputs(
        results,
        max_leads=MAX_LEADS,
    )

    leads = json.loads(
        inputs["candidate_jobs_json"]
    )

    verified = json.loads(
        inputs["verified_jobs_json"]
    )

    scores = json.loads(
        inputs["match_results_json"]
    )

    profile = json.loads(
        inputs["professional_profile_json"]
    )

    # No jobs are fully verified yet.
    if verified or scores or profile:

        raise ValueError(
            "No verified jobs, scores or profile "
            "are permitted at this stage."
        )

    # Enforce the lead limit.
    if len(leads) > MAX_LEADS:

        raise ValueError(
            "The candidate limit was exceeded."
        )

    # Never allow an unverified job to be promoted.
    if any(
        job.get("verification_status") != "unverified"
        for job in leads
    ):

        raise ValueError(
            "An unverified job was incorrectly promoted."
        )

    return inputs


# ============================================================
# STEP 2 — DISPLAY REAL JOB LEADS
# ============================================================

def show_preview(
    results: dict,
    inputs: dict[str, str],
) -> None:
    """
    Display real job-discovery statistics.

    These are source-based observations,
    NOT AI-generated recommendations.

    Unverified listings must be labeled clearly.
    """

    leads = json.loads(
        inputs["candidate_jobs_json"]
    )

    print(
        "\n========== LIVE JOBRADAR PREVIEW =========="
    )

    print(
        "Role matches:",
        results["title_matches"],
    )

    print(
        "ATS-live (not confirmed):",
        len(results["ats_live_candidates"]),
    )

    print(
        "Awaiting verification:",
        len(results["verification_queue"]),
    )

    print(
        "Rejected stale/closed:",
        len(results["excluded_stale_or_closed"]),
    )

    print(
        "Excluded foreign:",
        len(results["excluded_foreign"]),
    )

    print(
        "Excluded student:",
        len(results["excluded_seniority"]),
    )

    print(
        "Verified open:",
        len(results["verified_open"]),
    )

    print(
        "Public leads prepared for AI:",
        len(leads),
    )

    # Display the limited, privacy-safe data
    # that could later be provided to CrewAI.

    for index, job in enumerate(
        leads,
        start=1,
    ):

        print(
            f"\n{index}. "
            f"{job.get('title', 'Unknown')}"
        )

        print(
            "   Company:",
            job.get("company") or "Unknown",
        )

        print(
            "   Location:",
            job.get("location") or "Unknown",
        )

        print(
            "   Status: UNVERIFIED — "
            "not a recommendation"
        )

        print(
            "   Source:",
            job.get("url"),
        )

    print(
        "\nPrivate CV/profile transferred: NO"
    )

    print(
        "AI recommendations permitted: 0"
    )


# ============================================================
# STEP 3 — CONNECT EXISTING JOB DISCOVERY
# ============================================================

def fetch_and_prepare() -> tuple[dict, dict[str, str]]:
    """
    Run our existing real-data discovery pipeline.

    1. Retrieve Arbeitnow vacancies.
    2. Retrieve German employment agency vacancies.
    3. Apply our configured role and location filters.
    4. Apply freshness and vacancy-quality checks.
    5. Prepare safe CrewAI inputs.

    No cloud AI requests occur here.
    """

    records = collect_sources()

    results = prepare_jobs(
        records
    )

    inputs = build_inputs(
        results
    )

    return results, inputs


# ============================================================
# STEP 4 — OPTIONAL LIVE CREWAI EXECUTION
# ============================================================

def run_live_crew(
    inputs: dict[str, str],
) -> None:
    """
    Execute our real five-agent CrewAI system.

    IMPORTANT:
    This function runs only when explicitly requested
    with --run.

    It sends at most eight PUBLIC UNVERIFIED job leads
    to Ollama Cloud.

    It does not transmit CV data or make applications.

    The final approved recommendation count is
    enforced by Python, not by the language model.
    """

    llm = build_ollama_llm()

    crew = build_jobradar_crew(
        llm
    )

    print(
        "\nRunning CrewAI on at most "
        "8 PUBLIC UNVERIFIED leads..."
    )

    print(
        "This may use multiple Ollama Cloud requests."
    )

    print(
        "If prompted to share CrewAI traces, choose N."
    )

    try:

        # Start the actual hierarchical AI workflow.
        outcome = crew.kickoff(
            inputs=inputs
        )

    except Exception as error:

        # Prevent an accidental API key disclosure
        # if a network exception contains credentials.
        details = str(error)

        secret = os.environ.get(
            "OLLAMA_API_KEY",
            "",
        )

        if secret:

            details = details.replace(
                secret,
                "[REDACTED]",
            )

        raise RuntimeError(
            "CrewAI execution failed "
            f"({type(error).__name__}): "
            f"{details[:500]}"
        ) from None

    # Check that all four specialist tasks completed.
    task_outputs = getattr(
        outcome,
        "tasks_output",
        None,
    )

    if (
        not isinstance(task_outputs, list)
        or len(task_outputs) != 4
    ):

        raise RuntimeError(
            "CrewAI did not return all "
            "four task outputs."
        )

    raw = getattr(
        outcome,
        "raw",
        None,
    )

    text = (
        raw
        if isinstance(raw, str)
        else str(outcome)
    )

    # The AI must acknowledge our actual
    # zero-confirmed-open status.
    if MARKER not in text:

        raise RuntimeError(
            "AI draft omitted the required "
            "no-confirmed-jobs marker."
        )

    # Model-generated claims are not used
    # to override our deterministic filters.

    print(
        "PASS: Four CrewAI tasks completed "
        "with real public leads"
    )

    print(
        "PASS: Model acknowledged that "
        "no vacancy is confirmed"
    )

    print(
        "APPROVED JOB RECOMMENDATIONS: 0"
    )

    print(
        "Result:",
        MARKER,
    )

    print(
        "AI narrative withheld until "
        "final eligibility verification exists."
    )


# ============================================================
# STEP 5 — OFFLINE SECURITY TESTS
# ============================================================

def run_self_test() -> None:
    """
    Test privacy and verification controls without APIs.

    The test uses fictional public job metadata and
    intentionally adds private fields.

    None of the private information may appear in
    the prepared CrewAI payload.
    """

    fake = {
        "verified_open": [],

        "verification_queue": [
            {
                "title": "Data Analyst",
                "company": "Example Employer",
                "location": "Augsburg",
                "url": (
                    "https://example.org/jobs/one"
                ),
                "source": "Test source",

                # These fields must be excluded.
                "description": (
                    "DO NOT SEND RAW DESCRIPTION"
                ),
                "raw_cv": (
                    "DO NOT SEND PRIVATE CV"
                ),
                "private_email": (
                    "sensitive@example.org"
                ),
            }
        ],
    }

    inputs = build_inputs(
        fake
    )

    encoded = json.dumps(
        inputs,
        ensure_ascii=False,
    )

    leads = json.loads(
        inputs["candidate_jobs_json"]
    )

    # Test 1 — Correct verification status.
    assert len(leads) == 1

    assert (
        leads[0]["verification_status"]
        == "unverified"
    )

    print(
        "PASS: Real-lead payload remains unverified"
    )

    # Test 2 — Private information is excluded.
    private_values = (
        "DO NOT SEND RAW DESCRIPTION",
        "DO NOT SEND PRIVATE CV",
        "sensitive@example.org",
    )

    assert all(
        secret not in encoded
        for secret in private_values
    )

    print(
        "PASS: CV, contact data and "
        "descriptions excluded"
    )

    # Test 3 — No fabricated verified results.
    assert json.loads(
        inputs["verified_jobs_json"]
    ) == []

    assert json.loads(
        inputs["match_results_json"]
    ) == []

    assert json.loads(
        inputs["professional_profile_json"]
    ) == {}

    print(
        "PASS: No fabricated verified jobs, "
        "scores or profile"
    )

    # Test 4 — Unsupported confirmed-open claims
    # must be rejected.
    try:

        build_inputs({
            **fake,
            "verified_open": [
                {"title": "Fake"}
            ],
        })

    except ValueError:

        print(
            "PASS: Unsupported confirmed-open "
            "input rejected"
        )

    else:

        raise AssertionError(
            "Invalid verified-open input was accepted."
        )

    print(
        "PASS: Step 44 tests finished "
        "without API/LLM requests"
    )


# ============================================================
# STEP 6 — COMMAND-LINE INTERFACE
# ============================================================

def main() -> None:
    """
    Supported commands:

    --self-test:
        Run offline privacy tests.

    --preview:
        Fetch REAL job data and show unverified leads.
        Does not call Ollama Cloud.

    --run:
        Fetch real job data and run the CrewAI team.
        Makes multiple Ollama Cloud requests.
    """

    parser = argparse.ArgumentParser(
        description=(
            "JobRadar real-lead CrewAI runner"
        )
    )

    options = (
        parser.add_mutually_exclusive_group(
            required=True
        )
    )

    options.add_argument(
        "--self-test",
        action="store_true",
    )

    options.add_argument(
        "--preview",
        action="store_true",
    )

    options.add_argument(
        "--run",
        action="store_true",
    )

    args = parser.parse_args()

    if args.self_test:

        run_self_test()
        return

    # Both preview and run use REAL job APIs.
    results, inputs = fetch_and_prepare()

    show_preview(
        results,
        inputs,
    )

    if args.run:

        run_live_crew(
            inputs
        )

    else:

        print(
            "\nPreview only: no Ollama Cloud "
            "requests were made."
        )


if __name__ == "__main__":
    main()