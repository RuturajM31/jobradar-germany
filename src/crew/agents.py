"""
Day 11 — JobRadar Germany
Module: CrewAI Agent Definitions

THEORY
------
An Agent is a specialized AI worker with a role, goal, backstory,
and language model (LLM).

In a hierarchical CrewAI process, a manager delegates work to agents.

These definitions do not execute any tasks, contact job sites,
or call an LLM. The crew and tasks will be connected later.

DATA GOVERNANCE
---------------
- Python APIs and validators determine actual job facts.
- AI must not invent vacancies, dates, URLs, skills or match scores.
- No unverified listing is a confirmed recommendation.
- The user's complete CV and contact details remain local.
- Only a reviewed, sanitized professional profile may be used.
"""

from crewai import Agent


# ============================================================
# AGENT CONFIGURATIONS
# ============================================================

# Each entry defines a member of our AI recruitment team.
# Real CrewAI objects are created only when an LLM is supplied.

AGENT_SPECS = {

    # --------------------------------------------------------
    # AGENT 1 — RECRUITMENT MANAGER
    # --------------------------------------------------------

    "manager": {
        "role": "AI Recruitment Manager",

        "goal": (
            "Coordinate specialist agents to produce an "
            "evidence-based German job-search report. "
            "Enforce freshness, vacancy status, privacy "
            "and full-time employment requirements. "
            "Never present an unverified job as confirmed open."
        ),

        "backstory": (
            "You manage a careful recruitment-analytics team. "
            "You delegate work, review evidence and reject "
            "unsupported claims. JobRadar considers 43 configured "
            "role families, Germany-wide remote work, Augsburg, "
            "Munich, and relocation only for strong matches."
        ),

        "allow_delegation": True,
        "max_iter": 4,
    },

    # --------------------------------------------------------
    # AGENT 2 — JOB RESEARCHER
    # --------------------------------------------------------

    "researcher": {
        "role": "Job Researcher",

        "goal": (
            "Summarize only real jobs supplied by JobRadar's "
            "Python discovery pipeline. Preserve the exact "
            "source, title, location and URL. Identify missing "
            "information without fabricating it."
        ),

        "backstory": (
            "You are a recruitment data researcher experienced "
            "with German job titles and job-market terminology. "
            "You interpret structured source records and never "
            "claim to have searched websites without evidence."
        ),

        "allow_delegation": False,
        "max_iter": 2,
    },

    # --------------------------------------------------------
    # AGENT 3 — JOB VALIDATOR
    # --------------------------------------------------------

    "validator": {
        "role": "Job Validator",

        "goal": (
            "Review vacancy-quality evidence and identify "
            "expired, stale, foreign, student, uncertain or "
            "unsupported jobs. Preserve verification status "
            "exactly as provided by the validators."
        ),

        "backstory": (
            "You are a compliance-minded data quality specialist. "
            "A valid link or recent aggregator timestamp does not "
            "prove an employer is accepting applications. "
            "Never override the Python quality gate or upgrade "
            "an unverified vacancy."
        ),

        "allow_delegation": False,
        "max_iter": 2,
    },

    # --------------------------------------------------------
    # AGENT 4 — CV MATCH ANALYST
    # --------------------------------------------------------

    "analyst": {
        "role": "CV Match Analyst",

        "goal": (
            "Explain evidence-backed skill and experience "
            "alignment for vacancies that passed the verification "
            "gate. Use the separately calculated Python match "
            "score, not an invented AI score."
        ),

        "backstory": (
            "You are a business intelligence and analytics career "
            "evaluator. Your inputs may include only a reviewed "
            "professional profile, job requirements and a "
            "deterministic matching result. Never request, "
            "repeat or reveal personal CV identifiers."
        ),

        "allow_delegation": False,
        "max_iter": 2,
    },

    # --------------------------------------------------------
    # AGENT 5 — CAREER ADVISOR
    # --------------------------------------------------------

    "advisor": {
        "role": "Career Advisor",

        "goal": (
            "Produce concise, practical recommendations only "
            "for verified suitable vacancies. Consider full-time "
            "employment, language proficiency, work arrangement "
            "and relocation only for a strong match. "
            "Never apply to jobs automatically."
        ),

        "backstory": (
            "You are a Germany-based professional career adviser "
            "familiar with analytics, BI and business analyst "
            "hiring. Clearly separate confirmed opportunities "
            "from leads still requiring review."
        ),

        "allow_delegation": False,
        "max_iter": 2,
    },
}


# These are the four specialist workers.
# The manager coordinates them in the hierarchical crew.

WORKER_KEYS = (
    "researcher",
    "validator",
    "analyst",
    "advisor",
)


# ============================================================
# CREATE REAL CREWAI AGENTS
# ============================================================

def build_agents(llm) -> dict[str, Agent]:
    """
    Create five CrewAI Agent instances.

    THEORY:
    An Agent object combines a professional role with
    an LLM and execution settings.

    Args:
        llm:
            An explicitly configured crewai.LLM object.

    Returns:
        A dictionary containing:
        - manager
        - researcher
        - validator
        - analyst
        - advisor

    IMPORTANT:
    We never silently fall back to a paid AI provider.
    The application must explicitly supply its LLM.
    """

    if llm is None:
        raise ValueError(
            "Supply an explicitly configured LLM."
        )

    agents = {}

    for key, spec in AGENT_SPECS.items():

        agents[key] = Agent(
            role=spec["role"],
            goal=spec["goal"],
            backstory=spec["backstory"],

            # Shared model configuration.
            llm=llm,

            # Only the manager can delegate.
            allow_delegation=spec["allow_delegation"],

            # Keep iterations limited to control API usage.
            max_iter=spec["max_iter"],

            # Avoid repeated model failures.
            max_retry_limit=1,

            # Keep terminal output manageable.
            verbose=False,
        )

    return agents


# ============================================================
# OFFLINE CONFIGURATION TEST
# ============================================================

def run_self_test() -> None:
    """
    Check our agent definitions without invoking an LLM.

    THEORY:
    Configuration tests confirm that the correct team
    structure exists before we execute AI workflows.

    This is not yet a live CrewAI execution test.
    """

    # We require exactly five team members.
    assert len(AGENT_SPECS) == 5

    # Verify that all expected agents exist.
    assert set(AGENT_SPECS) == {
        "manager",
        *WORKER_KEYS,
    }

    # Every team member must have a unique role.
    assert len({
        spec["role"]
        for spec in AGENT_SPECS.values()
    }) == 5

    # The manager must be allowed to delegate.
    assert (
        AGENT_SPECS["manager"]["allow_delegation"]
        is True
    )

    # Specialist workers cannot delegate.
    assert all(
        not AGENT_SPECS[key]["allow_delegation"]
        for key in WORKER_KEYS
    )

    # Every agent requires a professional role,
    # a goal, a backstory and bounded iterations.
    assert all(
        spec["role"]
        and spec["goal"]
        and spec["backstory"]
        and 1 <= spec["max_iter"] <= 4
        for spec in AGENT_SPECS.values()
    )

    for key, spec in AGENT_SPECS.items():

        print(
            f"PASS: {key} — {spec['role']}"
        )

    print(
        "PASS: Five CrewAI agent definitions validated."
    )

    print(
        "No LLM calls or job API requests were made."
    )


# ============================================================
# PROGRAM ENTRY POINT
# ============================================================

if __name__ == "__main__":
    run_self_test()