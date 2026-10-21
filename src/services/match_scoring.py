"""
Day 11 — JobRadar Germany
Module: Local CV-to-Job Match Scoring

THEORY
------
A weighted scoring model must separate confirmed evidence from unknowns.

Missing job requirements or missing profile facts are NOT evidence of a
mismatch. The engine therefore calculates a possible score range whenever
one or more dimensions cannot be assessed.

Scoring weights:
    Technical skills       35 points
    Relevant experience    25 points
    Language requirements  15 points
    Location/work mode     15 points
    Seniority              10 points

DATA PRIVACY
------------
- All processing runs locally.
- No LLM, HTTP, or CV-upload operations occur in this module.
- Profile diagnostics print counts, not private profile contents.
- A match score NEVER confirms that a vacancy is open.
"""

import argparse
import json
import re
import unicodedata

from pathlib import Path

import yaml


# ============================================================
# STEP 1 — CONFIGURATION
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parents[2]

SCORING_CONFIG = (
    PROJECT_ROOT / "config" / "scoring.yaml"
)

PRIVATE_PROFILE = (
    PROJECT_ROOT
    / "data"
    / "private"
    / "professional_profile.json"
)

DIMENSIONS = (
    "technical_skills",
    "relevant_experience",
    "language_requirements",
    "location_work_mode",
    "seniority",
)

# Common European Framework of Reference.
CEFR = {
    "A1": 1,
    "A2": 2,
    "B1": 3,
    "B2": 4,
    "C1": 5,
    "C2": 6,
}

# Explicit aliases for commonly equivalent technical skills.
SKILL_ALIASES = {
    "microsoftpowerbi": "powerbi",
    "mspowerbi": "powerbi",
    "powerbi": "powerbi",
    "postgres": "postgresql",
    "postgresql": "postgresql",
    "msexcel": "excel",
    "microsoftexcel": "excel",
    "excel": "excel",
    "googleanalytics4": "ga4",
    "ga4": "ga4",
}


# ============================================================
# STEP 2 — NORMALIZE SKILLS AND EXPERIENCE
# ============================================================

def normalize_term(value: str) -> str:
    """
    Normalize technical skills and experience labels.

    Examples:
        Power BI -> powerbi
        PowerBI -> powerbi
        Microsoft Power BI -> powerbi

    Only explicit aliases are considered equivalent.
    """

    text = "".join(
        character
        for character in unicodedata.normalize(
            "NFKD",
            str(value).casefold(),
        )
        if not unicodedata.combining(character)
    )

    key = re.sub(
        r"[^a-z0-9]+",
        "",
        text,
    )

    return SKILL_ALIASES.get(
        key,
        key,
    )


def terms(value) -> list[str]:
    """
    Extract labels from strings, lists, or dictionaries.

    Our professional profile contains categorized skills:

        skills:
            Programming and Databases: [...]
            Business Intelligence: [...]
            Data Engineering: [...]
            Analytics: [...]

    This function reads those lists without inventing skills.
    """

    if isinstance(value, str):

        return (
            [value]
            if value.strip()
            else []
        )

    if isinstance(
        value,
        (list, tuple, set),
    ):

        return [
            label
            for item in value
            for label in terms(item)
        ]

    if isinstance(value, dict):

        found = []

        for key, item in value.items():

            if item is True:

                found.append(
                    str(key)
                )

            elif isinstance(
                item,
                (list, tuple, set, dict),
            ):

                found.extend(
                    terms(item)
                )

        return found

    return []


def normalized_set(value) -> set[str]:
    """
    Return unique normalized labels.

    This prevents equivalent spellings from being
    counted as separate skills.
    """

    return {
        key
        for item in terms(value)
        if (key := normalize_term(item))
    }


# ============================================================
# STEP 3 — LANGUAGE REQUIREMENTS
# ============================================================

def language_levels(value) -> dict[str, int]:
    """
    Convert documented CEFR levels into ordered numbers.

    Example:
        German B2 -> 4
        English C1 -> 5

    IMPORTANT:
    Our current profile does not contain language levels.

    Missing language evidence must remain UNKNOWN.
    """

    if not isinstance(value, dict):
        return {}

    levels = {}

    for language, proficiency in value.items():

        if isinstance(proficiency, str):

            code = proficiency.strip().upper()

            if code in CEFR:

                levels[
                    normalize_term(language)
                ] = CEFR[code]

    return levels


# ============================================================
# STEP 4 — LOAD SCORING CONFIGURATION
# ============================================================

def load_scoring_config(
    path: Path = SCORING_CONFIG,
) -> dict:
    """
    Read the actual JobRadar scoring configuration.

    EXACT PROJECT YAML STRUCTURE:

        scoring:
            weights:
                technical_skills: 35
                relevant_experience: 25
                language_requirements: 15
                location_work_mode: 15
                seniority: 10

            thresholds:
                high_match: 80
                potential_match: 60

            rules:
                ...

    IMPORTANT FIX:
    The threshold key is 'potential_match'.

    It is NOT 'potential'.
    """

    with Path(path).open(
        encoding="utf-8",
    ) as handle:

        raw = yaml.safe_load(
            handle
        )

    if not isinstance(raw, dict):

        raise ValueError(
            "Scoring YAML must be a dictionary."
        )

    # The actual YAML uses a top-level 'scoring' section.
    config = raw.get(
        "scoring",
        raw,
    )

    if not isinstance(config, dict):

        raise ValueError(
            "The scoring section must be a dictionary."
        )

    weights = config.get(
        "weights"
    )

    thresholds = config.get(
        "thresholds"
    )

    if (
        not isinstance(weights, dict)
        or not isinstance(thresholds, dict)
    ):

        raise ValueError(
            "Scoring YAML needs weights "
            "and thresholds mappings."
        )

    # --------------------------------------------------------
    # VALIDATE THE FIVE WEIGHTS
    # --------------------------------------------------------

    for name in DIMENSIONS:

        value = weights.get(
            name
        )

        if (
            type(value) is not int
            or value < 0
        ):

            raise ValueError(
                f"Invalid scoring weight: {name}"
            )

    total = sum(
        weights[name]
        for name in DIMENSIONS
    )

    if total != 100:

        raise ValueError(
            "Scoring weights must total exactly 100."
        )

    # --------------------------------------------------------
    # VALIDATE THE ACTUAL THRESHOLD KEYS
    # --------------------------------------------------------

    high = thresholds.get(
        "high_match"
    )

    # FIXED: The real YAML key is potential_match.
    potential = thresholds.get(
        "potential_match"
    )

    if (
        type(high) is not int
        or type(potential) is not int
    ):

        raise ValueError(
            "Both match thresholds must be integers. "
            "Expected high_match and potential_match."
        )

    if not (
        0 <= potential <= high <= 100
    ):

        raise ValueError(
            "Thresholds must satisfy "
            "0 <= potential_match <= high_match <= 100."
        )

    return config


# ============================================================
# STEP 5 — LOAD LOCAL PROFESSIONAL PROFILE
# ============================================================

def load_private_profile(
    path: Path = PRIVATE_PROFILE,
) -> dict:
    """
    Read the local professional profile.

    No data is sent to Ollama, CrewAI or external APIs.
    """

    with Path(path).open(
        encoding="utf-8",
    ) as handle:

        profile = json.load(
            handle
        )

    if not isinstance(profile, dict):

        raise ValueError(
            "Professional profile JSON must "
            "contain a dictionary."
        )

    return profile


def local_profile_summary(
    profile: dict,
) -> dict:
    """
    Extract professional evidence using the actual schema.

    Our profile contains:
    - skills: categorized dictionary
    - experience_areas: list
    - note: text

    No CEFR language information is currently present.

    Only explicitly recorded skills, experience areas
    and language levels are extracted.
    """

    if not isinstance(profile, dict):

        raise ValueError(
            "Professional profile must be a dictionary."
        )

    nested = profile.get(
        "professional_profile"
    )

    if isinstance(nested, dict):

        profile = nested

    skills = normalized_set(
        profile.get("skills")
        or profile.get("technical_skills")
    )

    # The actual field is experience_areas.
    domains = normalized_set(
        profile.get("experience_areas")
        or profile.get("experience_domains")
        or profile.get("domains")
    )

    languages = language_levels(
        profile.get("languages")
    )

    return {
        "skills": skills,
        "domains": domains,
        "languages": languages,
    }


# ============================================================
# STEP 6 — CALCULATE THE MATCH SCORE
# ============================================================

def score_job(
    profile: dict,
    job: dict,
    config: dict,
) -> dict:
    """
    Compare documented job requirements with local CV evidence.

    Expected job fields:
        required_skills
        required_domains
        required_languages
        geography
        seniority

    IMPORTANT:
    Our real job-requirements extraction is not yet
    connected to this engine.

    Unknown dimensions receive None, not invented points.

    A score never establishes vacancy availability.
    """

    if not isinstance(job, dict):

        raise ValueError(
            "Job must be a dictionary."
        )

    candidate = local_profile_summary(
        profile
    )

    weights = config["weights"]

    thresholds = config["thresholds"]

    points = {
        name: None
        for name in DIMENSIONS
    }

    notes = []

    # --------------------------------------------------------
    # DIMENSION 1 — TECHNICAL SKILLS (35)
    # --------------------------------------------------------

    required_skills = normalized_set(
        job.get("required_skills")
    )

    if (
        required_skills
        and candidate["skills"]
    ):

        matched = (
            required_skills
            & candidate["skills"]
        )

        points["technical_skills"] = (
            weights["technical_skills"]
            * len(matched)
            / len(required_skills)
        )

        notes.append(
            "Technical skills evidenced: "
            f"{len(matched)}/{len(required_skills)}"
        )

    else:

        notes.append(
            "Technical skill requirements "
            "or candidate evidence missing"
        )

    # --------------------------------------------------------
    # DIMENSION 2 — RELEVANT EXPERIENCE (25)
    # --------------------------------------------------------

    required_domains = normalized_set(
        job.get("required_domains")
    )

    if (
        required_domains
        and candidate["domains"]
    ):

        matched = (
            required_domains
            & candidate["domains"]
        )

        points["relevant_experience"] = (
            weights["relevant_experience"]
            * len(matched)
            / len(required_domains)
        )

        notes.append(
            "Experience domains evidenced: "
            f"{len(matched)}/{len(required_domains)}"
        )

    else:

        notes.append(
            "Experience requirements "
            "or candidate evidence missing"
        )

    # --------------------------------------------------------
    # DIMENSION 3 — LANGUAGE REQUIREMENTS (15)
    # --------------------------------------------------------

    required_languages = language_levels(
        job.get("required_languages")
    )

    # All required languages must have documented levels.
    if (
        required_languages
        and all(
            language in candidate["languages"]
            for language in required_languages
        )
    ):

        satisfied = sum(
            candidate["languages"][language] >= level
            for language, level in required_languages.items()
        )

        points["language_requirements"] = (
            weights["language_requirements"]
            * satisfied
            / len(required_languages)
        )

        unmet = [
            language
            for language, level in required_languages.items()
            if candidate["languages"][language] < level
        ]

        if unmet:

            notes.append(
                "Insufficient documented CEFR level: "
                + ", ".join(sorted(unmet))
            )

        else:

            notes.append(
                "Documented language requirements met"
            )

    else:

        notes.append(
            "Language requirements or candidate "
            "CEFR levels unconfirmed"
        )

    # --------------------------------------------------------
    # DIMENSION 4 — LOCATION / WORK MODE (15)
    # --------------------------------------------------------

    geography = job.get(
        "geography"
    )

    if geography in {
        "preferred_city",
        "nearby_area",
        "remote_germany",
    }:

        points["location_work_mode"] = float(
            weights["location_work_mode"]
        )

    elif geography == "other_germany":

        points["location_work_mode"] = (
            weights["location_work_mode"]
            * 0.5
        )

        notes.append(
            "Relocation requires a strong match "
            "and manual approval"
        )

    else:

        notes.append(
            "German location or remote eligibility unknown"
        )

    # --------------------------------------------------------
    # DIMENSION 5 — SENIORITY (10)
    # --------------------------------------------------------

    seniority = str(
        job.get("seniority") or ""
    ).strip().casefold()

    if seniority in {
        "mid-level",
        "senior",
    }:

        points["seniority"] = float(
            weights["seniority"]
        )

    elif seniority in {
        "junior",
        "lead",
    }:

        points["seniority"] = (
            weights["seniority"]
            * 0.5
        )

    else:

        notes.append(
            "Seniority preference unconfirmed"
        )

    # ========================================================
    # STEP 7 — SCORE RANGE
    # ========================================================

    minimum = round(
        sum(
            value
            for value in points.values()
            if value is not None
        ),
        2,
    )

    unknown = [
        name
        for name, value in points.items()
        if value is None
    ]

    maximum = round(
        minimum
        + sum(
            weights[name]
            for name in unknown
        ),
        2,
    )

    # An exact score requires evidence for all dimensions.
    exact = (
        None
        if unknown
        else minimum
    )

    # ========================================================
    # STEP 8 — MATCH CLASSIFICATION
    # ========================================================

    if exact is None:

        band = "insufficient_evidence"

    elif exact >= thresholds["high_match"]:

        band = "high_match_preliminary"

    elif exact >= thresholds["potential_match"]:

        # FIXED: Use the actual threshold key.
        band = "potential_match_preliminary"

    else:

        band = "low_match_preliminary"

    relocation_review = (
        geography == "other_germany"
        and exact is not None
        and exact >= thresholds["high_match"]
    )

    # ========================================================
    # STEP 9 — RETURN RESULTS
    # ========================================================

    return {
        "score": exact,

        "possible_score_range": [
            minimum,
            maximum,
        ],

        "band": band,

        "points_by_dimension": {
            name: (
                round(value, 2)
                if value is not None
                else None
            )
            for name, value in points.items()
        },

        "unknown_dimensions": unknown,

        "relocation_manual_review": relocation_review,

        # A score never approves an unverified vacancy.
        "approved_recommendation": False,

        "explanations": notes,
    }


# ============================================================
# STEP 10 — OFFLINE TESTS
# ============================================================

def run_self_test() -> None:
    """
    Test against the real YAML configuration.

    The professional profile used for scoring tests
    is fictional and contains no personal information.

    Tests:
    - Correct nested YAML
    - Actual threshold names
    - Categorized skills
    - Experience areas
    - Perfect score
    - Missing requirements
    - Advanced German requirement
    - Missing language levels
    - Relocation restrictions
    """

    config = load_scoring_config()

    assert sum(
        config["weights"][name]
        for name in DIMENSIONS
    ) == 100

    # Verify the exact values the user configured.
    assert config["thresholds"]["high_match"] == 80

    assert config["thresholds"]["potential_match"] == 60

    print(
        "PASS: Nested scoring YAML and "
        "exact threshold keys (80/60) loaded"
    )

    # Fictional candidate using our real profile structure.
    example_profile = {
        "skills": {
            "Programming and Databases": [
                "SQL",
                "Python",
            ],

            "Business Intelligence": [
                "Microsoft Power BI",
            ],
        },

        "experience_areas": [
            "Business Intelligence",
            "Reporting",
        ],

        "languages": {
            "German": "B2",
            "English": "C1",
        },
    }

    summary = local_profile_summary(
        example_profile
    )

    assert len(summary["skills"]) == 3

    assert len(summary["domains"]) == 2

    print(
        "PASS: Categorized skills and "
        "experience_areas extracted"
    )

    # Fictional job with explicit requirements.
    full_job = {
        "required_skills": [
            "SQL",
            "PowerBI",
        ],

        "required_domains": [
            "Business Intelligence",
        ],

        "required_languages": {
            "German": "B2",
            "English": "B2",
        },

        "geography": "preferred_city",

        "seniority": "Senior",
    }

    # TEST 1 — PERFECT MATCH
    perfect = score_job(
        example_profile,
        full_job,
        config,
    )

    assert perfect["score"] == 100.0

    assert perfect["approved_recommendation"] is False

    print(
        "PASS: Evidence-supported complete match "
        "scores 100/100"
    )

    # TEST 2 — INCOMPLETE JOB REQUIREMENTS
    incomplete = score_job(
        example_profile,
        {
            "geography": "preferred_city",
            "seniority": "Senior",
        },
        config,
    )

    assert incomplete["score"] is None

    assert (
        incomplete["possible_score_range"]
        == [25.0, 100.0]
    )

    print(
        "PASS: Missing job requirements "
        "return a score range"
    )

    # TEST 3 — ADVANCED GERMAN REQUIREMENT
    advanced = score_job(
        example_profile,
        {
            **full_job,

            "required_languages": {
                "German": "C1",
                "English": "B2",
            },
        },
        config,
    )

    assert advanced["score"] == 92.5

    print(
        "PASS: Higher German CEFR requirement flagged"
    )

    # TEST 4 — UNKNOWN CANDIDATE LANGUAGES
    without_languages = {
        key: value
        for key, value in example_profile.items()
        if key != "languages"
    }

    language_unknown = score_job(
        without_languages,
        full_job,
        config,
    )

    assert language_unknown["score"] is None

    assert (
        "language_requirements"
        in language_unknown["unknown_dimensions"]
    )

    print(
        "PASS: Missing profile languages remain unknown"
    )

    # TEST 5 — RELOCATION
    relocation = score_job(
        example_profile,
        {
            **full_job,
            "geography": "other_germany",
        },
        config,
    )

    assert relocation["relocation_manual_review"] is True

    assert relocation["approved_recommendation"] is False

    print(
        "PASS: Strong relocation leads "
        "still require manual approval"
    )

    print(
        "PASS: All scoring tests completed; "
        "no CV upload or API calls"
    )


# ============================================================
# STEP 11 — PRIVATE PROFILE CHECK
# ============================================================

def run_profile_check() -> None:
    """
    Display only evidence counts from the local profile.

    No personal details are printed or uploaded.
    """

    profile = local_profile_summary(
        load_private_profile()
    )

    print(
        "PASS: Local professional profile loaded"
    )

    print(
        "Skill entries:",
        len(profile["skills"]),
    )

    print(
        "Experience domains:",
        len(profile["domains"]),
    )

    print(
        "CEFR language entries:",
        len(profile["languages"]),
    )

    if not profile["languages"]:

        print(
            "INFO: Language matching remains unknown "
            "until CEFR levels are documented"
        )

    print(
        "No private profile contents "
        "displayed or transmitted"
    )


# ============================================================
# STEP 12 — COMMAND-LINE INTERFACE
# ============================================================

def main() -> None:
    """
    Available commands:

    --self-test
        Validate scoring against actual YAML.

    --profile-check
        Check local profile evidence counts.

    Both commands work without Internet access.
    """

    parser = argparse.ArgumentParser(
        description="JobRadar local 100-point match scoring"
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
        "--profile-check",
        action="store_true",
    )

    args = parser.parse_args()

    if args.self_test:

        run_self_test()

    else:

        run_profile_check()


if __name__ == "__main__":
    main()