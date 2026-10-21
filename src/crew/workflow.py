"""
Day 11 — JobRadar Germany
Module: Hierarchical CrewAI Workflow

THEORY
------
A Crew connects Agents, Tasks and a Process.

Sequential process:
    Tasks follow a fixed order.

Hierarchical process:
    A manager coordinates specialists and reviews their work.

Our architecture:

    AI Recruitment Manager
        |
        +-- Job Researcher
        +-- Job Validator
        +-- CV Match Analyst
        +-- Career Advisor

PRIVACY AND SAFETY
------------------
- The complete CV remains local.
- No contact information is included in crew inputs.
- Job descriptions cannot override verification rules.
- Unverified vacancies cannot become recommendations.
- No LLM is called during the offline self-test.
"""

import argparse
import json
from urllib.parse import urlsplit

from crewai import Crew, Process

from src.crew.agents import (
    AGENT_SPECS,
    WORKER_KEYS,
    build_agents,
)

from src.crew.tasks import (
    INPUT_FIELDS,
    TASK_ORDER,
    TASK_SPECS,
    build_tasks,
)


# ============================================================
# STEP 1 — BUILD THE HIERARCHICAL CREW
# ============================================================

def build_jobradar_crew(llm) -> Crew:
    """
    Build our complete five-agent CrewAI system.

    Args:
        llm:
            Explicitly configured CrewAI language model.

    Returns:
        A hierarchical CrewAI Crew object.

    IMPORTANT:
    This function constructs the crew but does not
    execute tasks or contact the language model.

    The caller must explicitly invoke kickoff().
    """

    if llm is None:
        raise ValueError(
            "An explicitly configured LLM is required."
        )

    # Create our five specialist/manager objects.
    agents = build_agents(llm)

    # Create four tasks and their context dependencies.
    tasks = build_tasks(agents)

    # The manager is supplied separately.
    #
    # Only the four worker agents belong in the
    # agents list of the hierarchical Crew.
    workers = [
        agents[key]
        for key in WORKER_KEYS
    ]

    crew = Crew(
        agents=workers,
        tasks=tasks,

        # Hierarchical execution.
        process=Process.hierarchical,

        # Our custom recruitment manager.
        manager_agent=agents["manager"],

        # Avoid unnecessary additional features.
        verbose=False,
        memory=False,
        planning=False,
        cache=False,
    )

    return crew


# ============================================================
# STEP 2 — PREPARE SAFE INPUTS
# ============================================================

def safe_job_record(job: dict) -> dict:
    """
    Create a limited job record for AI processing.

    THEORY:
    An allowlist explicitly defines which fields
    are permitted to leave the Python data pipeline.

    Never send raw CV text or complete source records.

    We only pass professional job information.
    """

    allowed_fields = (
        "title",
        "company",
        "location",
        "source",
        "url",
        "matched_role",
        "role_priority",
        "geography",
        "seniority",
        "age_days",
    )

    result = {}

    for field in allowed_fields:

        value = job.get(field)

        if value is None:
            result[field] = None

        elif isinstance(value, (str, int, float, bool)):
            result[field] = (
                value[:300]
                if isinstance(value, str)
                else value
            )

    # Only HTTPS source links are retained.
    url = result.get("url")

    if not (
        isinstance(url, str)
        and urlsplit(url).scheme == "https"
        and urlsplit(url).hostname
    ):
        result["url"] = None

    # A lead cannot become verified through this function.
    result["verification_status"] = "unverified"

    return result


def prepare_crew_inputs(
    discovery_results: dict,
    max_leads: int = 8,
) -> dict[str, str]:
    """
    Prepare safe inputs for CrewAI tasks.

    Inputs required by our existing tasks:
    - candidate_jobs_json
    - verified_jobs_json
    - match_results_json
    - professional_profile_json

    SECURITY:
    This initial version does not transmit any CV profile.

    The professional profile must be explicitly reviewed
    before it can be included in an LLM request.

    IMPORTANT:
    Our discovery pipeline does not yet produce confirmed
    open vacancies. We therefore refuse non-empty
    verified_open inputs until the final verification
    contract is implemented.
    """

    if not isinstance(discovery_results, dict):
        raise ValueError(
            "Discovery results must be a dictionary."
        )

    if not 1 <= max_leads <= 10:
        raise ValueError(
            "max_leads must be between 1 and 10."
        )

    # Never trust an unsupported claim of verification.
    if discovery_results.get("verified_open"):
        raise ValueError(
            "Confirmed-open input requires the final "
            "employer verification contract, which "
            "is not implemented yet."
        )

    queue = discovery_results.get(
        "verification_queue",
        [],
    )

    if not isinstance(queue, list):
        raise ValueError(
            "Verification queue must be a list."
        )

    # Send only limited, explicitly approved fields.
    candidates = [
        safe_job_record(job)
        for job in queue[:max_leads]
        if isinstance(job, dict)
    ]

    return {
        "candidate_jobs_json": json.dumps(
            candidates,
            ensure_ascii=False,
        ),

        # There are currently no confirmed vacancies.
        "verified_jobs_json": "[]",

        # CV match scoring has not been implemented yet.
        "match_results_json": "[]",

        # Do not send the private CV profile automatically.
        "professional_profile_json": "{}",
    }


# ============================================================
# STEP 3 — VERIFY THE WORKFLOW OFFLINE
# ============================================================

def run_self_test() -> None:
    """
    Verify workflow structure without any LLM calls.

    Checks:
    1. Hierarchical process exists.
    2. Exactly five agent definitions exist.
    3. Four specialist tasks exist.
    4. The manager is not included among workers.
    5. Task input fields are complete.
    6. No personal CV data enters the prepared payload.
    7. No unverified job is promoted to confirmed status.
    """

    assert Process.hierarchical is not None

    print("PASS: Hierarchical process available")

    assert len(AGENT_SPECS) == 5

    assert len(WORKER_KEYS) == 4

    assert "manager" not in WORKER_KEYS

    print("PASS: One manager and four workers configured")

    assert len(TASK_ORDER) == 4

    assert set(TASK_ORDER) == set(TASK_SPECS)

    print("PASS: Four specialist tasks configured")

    # An artificial record used only for this offline test.
    test_discovery = {
        "verified_open": [],

        "verification_queue": [
            {
                "title": "Data Analyst",
                "company": "Example Employer",
                "location": "Augsburg",
                "source": "Test source",
                "url": "https://example.org/job/123",
                "matched_role": "Data Analyst",
                "role_priority": "priority_a",
                "geography": "preferred_city",
                "age_days": 5,

                # Private information must never appear
                # in the CrewAI task inputs.
                "private_email": "private@example.org",
                "private_phone": "000000000",
                "raw_cv": "PRIVATE_CV_CONTENT",
            }
        ],
    }

    inputs = prepare_crew_inputs(test_discovery)

    assert set(inputs) == set(INPUT_FIELDS)

    print("PASS: All task input fields prepared")

    serialized = json.dumps(
        inputs,
        ensure_ascii=False,
    )

    assert "private@example.org" not in serialized

    assert "000000000" not in serialized

    assert "PRIVATE_CV_CONTENT" not in serialized

    print("PASS: Private CV fields excluded")

    assert json.loads(
        inputs["verified_jobs_json"]
    ) == []

    assert json.loads(
        inputs["match_results_json"]
    ) == []

    print("PASS: Unverified jobs remain unverified")

    # Creating the CrewAI objects will be tested
    # after configuring the model.
    #
    # This self-test intentionally does not
    # instantiate or execute an LLM.

    print(
        "\nPASS: Hierarchical workflow "
        "configuration validated"
    )

    print(
        "No LLM requests or job API requests were made."
    )


# ============================================================
# PROGRAM ENTRY POINT
# ============================================================

def main() -> None:
    """
    Run the offline workflow configuration test.

    Real CrewAI execution will be introduced only
    after explicit Ollama Cloud configuration.
    """

    parser = argparse.ArgumentParser(
        description="JobRadar hierarchical CrewAI workflow"
    )

    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Validate the workflow without LLM requests",
    )

    args = parser.parse_args()

    if args.self_test:
        run_self_test()

    else:
        parser.print_help()


if __name__ == "__main__":
    main()