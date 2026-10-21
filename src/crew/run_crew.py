"""
Day 11 — JobRadar Germany
Module: Controlled CrewAI Execution

THEORY
------
A Crew is a runtime that coordinates agents and tasks.

Calling crew.kickoff(inputs=...) starts the actual
language-model workflow.

The first run uses NO real jobs and NO CV data.

This isolates model connectivity and team coordination
from vacancy-search uncertainties.

IMPORTANT:
The LLM generates narrative text.

Deterministic Python controls whether job recommendations
are allowed. For this first test, that number is zero.
"""

import argparse
import json
import os
import re

from src.crew.llm_config import build_ollama_llm

from src.crew.workflow import (
    build_jobradar_crew,
    prepare_crew_inputs,
)


# ============================================================
# CONFIGURATION
# ============================================================

NO_JOBS_MARKER = "NO_CONFIRMED_OPEN_JOBS"

EXPECTED_TASK_COUNT = 4


# ============================================================
# STEP 1 — PREPARE SAFE INPUTS
# ============================================================

def build_smoke_inputs() -> dict[str, str]:
    """
    Prepare a minimal, nonprivate CrewAI input payload.

    THEORY:
    A smoke test verifies whether a system can execute
    its essential workflow.

    We intentionally exclude:
    - Real vacancies
    - Private CV information
    - Professional profile
    - Match scores

    This helps verify the AI architecture without
    exposing personal information.
    """

    discovery = {
        "verified_open": [],
        "verification_queue": [],
        "ats_live_candidates": [],
    }

    # Reuse the controlled input preparation
    # implemented in Step 41.
    inputs = prepare_crew_inputs(
        discovery,
        max_leads=1,
    )

    required_fields = (
        "candidate_jobs_json",
        "verified_jobs_json",
        "match_results_json",
        "professional_profile_json",
    )

    # Fail before making any cloud request if
    # the expected input contract is incomplete.
    for field in required_fields:

        if field not in inputs:
            raise ValueError(
                f"Missing crew input: {field}"
            )

    if json.loads(
        inputs["candidate_jobs_json"]
    ) != []:

        raise ValueError(
            "Smoke test must not transmit real job leads."
        )

    if json.loads(
        inputs["verified_jobs_json"]
    ) != []:

        raise ValueError(
            "Smoke test cannot include verified jobs."
        )

    if json.loads(
        inputs["match_results_json"]
    ) != []:

        raise ValueError(
            "Smoke test cannot include CV match scores."
        )

    if json.loads(
        inputs["professional_profile_json"]
    ) != {}:

        raise ValueError(
            "Smoke test cannot include a professional profile."
        )

    return inputs


# ============================================================
# STEP 2 — VALIDATE THE AI OUTPUT
# ============================================================

def validate_ai_output(
    text: str,
) -> tuple[bool, str]:
    """
    Apply a basic deterministic output check.

    THEORY:
    AI output should not be trusted merely because
    it sounds convincing.

    For our controlled test, the result must:

    1. Contain NO_CONFIRMED_OPEN_JOBS.
    2. Contain no invented HTTP job links.
    3. Not be empty.

    IMPORTANT:
    This is not a complete hallucination detector.

    Passing these checks does not establish that
    all natural-language claims are correct.
    """

    if (
        not isinstance(text, str)
        or not text.strip()
    ):

        return (
            False,
            "The AI returned an empty report.",
        )

    # No confirmed vacancy exists in our test dataset.
    if NO_JOBS_MARKER not in text:

        return (
            False,
            "The AI omitted the required no-jobs marker.",
        )

    # The model was given zero job URLs.
    # Therefore, any generated URL is unsupported.
    if re.search(
        r"https?://",
        text,
        flags=re.IGNORECASE,
    ):

        return (
            False,
            "The AI included a URL despite receiving no job data.",
        )

    return (
        True,
        "Required no-jobs marker present; no URLs invented.",
    )


# ============================================================
# STEP 3 — OFFLINE UNIT TESTS
# ============================================================

def run_self_test() -> None:
    """
    Verify privacy and output controls without an LLM.

    Tests:
    - Required task inputs exist
    - No real job data is included
    - No CV information is transmitted
    - Valid no-jobs response is accepted
    - Unsupported recommendation is rejected
    - Invented URL is rejected

    No network calls occur.
    """

    inputs = build_smoke_inputs()

    assert len(inputs) == 4

    print(
        "PASS: Four CrewAI task inputs prepared"
    )

    assert all(
        json.loads(value) in ([], {})
        for value in inputs.values()
    )

    print(
        "PASS: No CV, professional profile or "
        "real jobs in payload"
    )

    # Correct AI response.
    accepted, _ = validate_ai_output(
        "The team completed review. "
        "NO_CONFIRMED_OPEN_JOBS"
    )

    assert accepted

    print(
        "PASS: Correct no-jobs response accepted"
    )

    # Unsupported AI recommendation.
    invalid, _ = validate_ai_output(
        "Here are some excellent job recommendations."
    )

    assert not invalid

    print(
        "PASS: Unsupported recommendation response rejected"
    )

    # Invented job URL.
    invalid_url, _ = validate_ai_output(
        "NO_CONFIRMED_OPEN_JOBS "
        "https://example.com/fake-job"
    )

    assert not invalid_url

    print(
        "PASS: Invented links rejected"
    )

    print(
        "PASS: Step 43 offline checks completed; "
        "no LLM requests"
    )


# ============================================================
# STEP 4 — EXECUTE THE REAL CREWAI SYSTEM
# ============================================================

def run_live_smoke() -> None:
    """
    Execute the full hierarchical CrewAI workflow.

    Architecture:

        AI Recruitment Manager
                 |
        ---------------------
        |     |      |      |
      Research Validate Match Advise

    The manager coordinates the specialist tasks.

    This operation sends a controlled, nonprivate
    payload to Ollama Cloud.

    IMPORTANT:
    Hierarchical execution may make multiple model
    requests and consume free-tier usage.

    This function does NOT search live jobs and
    does NOT automatically apply to vacancies.
    """

    # Always prepare and validate inputs first.
    inputs = build_smoke_inputs()

    # Create the explicit Ollama Cloud LLM.
    llm = build_ollama_llm()

    # Build our real CrewAI hierarchy.
    crew = build_jobradar_crew(
        llm
    )

    print(
        "Starting hierarchical CrewAI smoke test..."
    )

    print(
        "Manager: 1 | Specialists: 4 | Tasks: 4"
    )

    print(
        "Real jobs transmitted: 0 | CV transmitted: 0"
    )

    print(
        "The model may make multiple cloud requests."
    )

    try:

        # ====================================================
        # THIS STARTS THE ACTUAL AI WORKFLOW
        # ====================================================

        output = crew.kickoff(
            inputs=inputs
        )

    except Exception as error:

        # Do not accidentally display an API key if
        # an HTTP client includes it in an error message.

        message = str(error)

        secret = os.environ.get(
            "OLLAMA_API_KEY",
            "",
        )

        if secret:

            message = message.replace(
                secret,
                "[REDACTED]",
            )

        raise RuntimeError(
            "Crew execution failed "
            f"({type(error).__name__}): "
            f"{message[:700]}"
        ) from None

    # ========================================================
    # STEP 5 — INSPECT TASK EXECUTION
    # ========================================================

    task_outputs = getattr(
        output,
        "tasks_output",
        None,
    )

    completed = (
        len(task_outputs)
        if isinstance(task_outputs, list)
        else 0
    )

    if completed != EXPECTED_TASK_COUNT:

        raise RuntimeError(
            f"Expected {EXPECTED_TASK_COUNT} task outputs; "
            f"received {completed}. "
            "Review CrewAI execution."
        )

    print(
        f"PASS: CrewAI produced {completed} "
        "specialist task outputs"
    )

    # Extract the final CrewAI answer.
    raw = getattr(
        output,
        "raw",
        None,
    )

    report = (
        raw
        if isinstance(raw, str)
        else str(output)
    )

    # ========================================================
    # STEP 6 — APPLY THE OUTPUT SAFETY CHECK
    # ========================================================

    valid, explanation = validate_ai_output(
        report
    )

    if not valid:

        print(
            "WARNING: AI report did not pass "
            "the safety check."
        )

        print(
            "Reason:",
            explanation,
        )

        print(
            "APPROVED JOB RECOMMENDATIONS: 0"
        )

        raise RuntimeError(
            "AI report rejected by deterministic "
            "output check."
        )

    print(
        "PASS: AI returned the required "
        "no-confirmed-jobs marker"
    )

    print(
        "PASS: No unsupported job URLs "
        "in final AI output"
    )

    # This number is controlled by Python,
    # not calculated or invented by the LLM.
    print(
        "APPROVED JOB RECOMMENDATIONS: 0"
    )

    print(
        "Result:",
        NO_JOBS_MARKER,
    )

    # The model output remains a draft.
    # Display only a limited preview.
    print(
        "\nAI report preview "
        "(untrusted draft, not job advice):"
    )

    print(
        report[:900]
    )

    print(
        "\nPASS: First hierarchical CrewAI "
        "execution completed"
    )


# ============================================================
# STEP 7 — COMMAND-LINE INTERFACE
# ============================================================

def main() -> None:
    """
    Available commands:

    --self-test:
        Run offline privacy and output-validation tests.

    --run:
        Execute the real five-agent CrewAI system
        through Ollama Cloud.
    """

    parser = argparse.ArgumentParser(
        description=(
            "JobRadar first controlled "
            "hierarchical CrewAI run"
        )
    )

    choices = (
        parser.add_mutually_exclusive_group(
            required=True
        )
    )

    choices.add_argument(
        "--self-test",
        action="store_true",
    )

    choices.add_argument(
        "--run",
        action="store_true",
    )

    args = parser.parse_args()

    if args.self_test:

        run_self_test()

    else:

        try:

            run_live_smoke()

        except (
            ValueError,
            RuntimeError,
        ) as error:

            parser.exit(
                1,
                f"ERROR: {error}\n",
            )


if __name__ == "__main__":
    main()