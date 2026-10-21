"""
JobRadar Germany — Job Requirements and Local Match Evidence

THEORY
------
Job titles are insufficient evidence for CV matching.

This module searches actual job descriptions for qualification
requirements. It records the evidence behind each extracted term.

Extraction is rule-based and provisional. It cannot prove that
the extracted terms are a complete list of mandatory requirements.

PRIVACY
-------
No LLM calls.
No CV uploads.
No automatic applications.
No confirmed-open recommendations.
"""

from __future__ import annotations

import argparse
import re

from collections import defaultdict
from html.parser import HTMLParser


# ============================================================
# 1. EXPLICIT TECHNICAL SKILL VOCABULARY
# ============================================================

SKILLS: dict[str, str] = {
    r"\b(?:microsoft\s*)?power\s*bi\b": "Power BI",
    r"\bpython\b": "Python",
    r"(?<![\w-])sql(?![\w-])": "SQL",
    r"\b(?:microsoft\s*)?excel\b": "Excel",
    r"\btableau\b": "Tableau",
    r"\blooker(?:\s+studio)?\b": "Looker",
    r"\b(?:google\s+analytics\s*4|ga\s*4)\b": "GA4",
    r"\b(?:microsoft\s*)?fabric\b": "Microsoft Fabric",
    r"\bdatabricks\b": "Databricks",
    r"\bsnowflake\b": "Snowflake",
    r"\bbigquery\b": "BigQuery",
    r"\bpostgres(?:ql)?\b": "PostgreSQL",
    r"\bmysql\b": "MySQL",
    r"\b(?:t-?sql|transact.sql)\b": "T-SQL",
    r"\bspark\b": "Spark",
    r"\b(?:apache\s+)?airflow\b": "Airflow",
    r"(?<!\w)dbt(?!\w)": "dbt",
    r"\b(?:etl|elt)\b": "ETL",
    r"\bazure\b": "Azure",
    r"\baws\b": "AWS",
    r"\b(?:google\s+cloud|gcp)\b": "GCP",
    r"\b(?:sap\s+(?:bw|hana|s/4hana)|sap)\b": "SAP",
    r"\bcelonis\b": "Celonis",
    r"\bsalesforce\b": "Salesforce",
    r"\bhubspot\b": "HubSpot",
    r"\bjira\b": "Jira",
    r"\bconfluence\b": "Confluence",
    r"\bpandas\b": "Pandas",
    r"\bnumpy\b": "NumPy",
    r"\bscikit[ -]?learn\b": "scikit-learn",
    r"\b(?:a/b\s+test(?:ing)?|ab\s+test(?:ing)?)\b": "A/B testing",
    r"\b(?:machine\s+learning|maschinelles\s+lernen)\b": "Machine Learning",
    r"\b(?:statistics|statistik)\b": "Statistics",
    r"\b(?:requirements\s+engineering|anforderungsmanagement)\b": "Requirements Engineering",
    r"\b(?:data\s+visuali[sz]ation|datenvisualisierung)\b": "Data Visualization",
}


# ============================================================
# 2. PROFESSIONAL EXPERIENCE DOMAINS
# ============================================================

DOMAINS: dict[str, str] = {
    r"\b(?:business\s+intelligence)\b": "Business Intelligence",
    r"\b(?:reporting|berichtswesen)\b": "Reporting",
    r"\b(?:data\s+analytics|datenanalyse|data\s+analysis)\b": "Data Analytics",
    r"\b(?:business\s+analysis|business\s+analyse)\b": "Business Analysis",
    r"\b(?:marketing\s+analytics|marketinganalyse)\b": "Marketing Analytics",
    r"\b(?:product\s+analytics|produktanalytik)\b": "Product Analytics",
    r"\b(?:supply\s+chain|lieferkette)\b": "Supply Chain",
    r"\b(?:controlling|financial\s+planning)\b": "Controlling",
    r"\b(?:e[ -]?commerce|onlinehandel)\b": "E-Commerce",
    r"\b(?:data\s+engineering|datenengineering)\b": "Data Engineering",
    r"\b(?:data\s+warehouse|data\s+warehousing|datenwarehouse)\b": "Data Warehousing",
    r"\b(?:process\s+mining|prozessanalyse)\b": "Process Mining",
    r"\b(?:crm|customer\s+relationship\s+management)\b": "CRM",
    r"\b(?:logistics|logistik)\b": "Logistics",
}


# ============================================================
# 3. REQUIREMENT SECTION DETECTION
# ============================================================

# Identify explicit job-qualification headings in EN/DE.
START = re.compile(
    r"^(?:requirements|requirements\s*&\s*qualifications|"
    r"qualifications|your\s+profile|your\s+qualifications|"
    r"what\s+(?:you(?:'ll|\s+will)?\s+bring|"
    r"we(?:'re|\s+are)\s+looking\s+for)|"
    r"must[- ]haves?|anforderungen|voraussetzungen|"
    r"qualifikationen|(?:dein|ihr|deine|ihre)\s+profil|"
    r"das\s+bringst\s+du\s+mit|was\s+du\s+mitbringst|"
    r"das\s+erwarten\s+wir|"
    r"(?:deine|ihre)\s+qualifikationen|"
    r"das\s+zeichnet\s+dich\s+aus)\s*:?\s*(.*)$",
    re.IGNORECASE,
)

# Stop reading qualification requirements once the
# advertisement enters a different section.
STOP = re.compile(
    r"^(?:benefits|what\s+we\s+offer|our\s+offer|"
    r"about\s+us|your\s+benefits|responsibilities|"
    r"your\s+tasks|the\s+role|wir\s+bieten|"
    r"deine\s+vorteile|ihre\s+vorteile|"
    r"deine\s+aufgaben|ihre\s+aufgaben|"
    r"über\s+uns|bewerbung|application\s+process|"
    r"kontakt|contact)\s*:?\s*$",
    re.IGNORECASE,
)

# Preferred skills must not automatically be classified
# as mandatory requirements.
PREFERRED = re.compile(
    r"\b(?:nice\s+to\s+have|preferred|optional|"
    r"a\s+plus|wünschenswert|von\s+vorteil|"
    r"idealerweise|bonus)\b",
    re.IGNORECASE,
)

# If an advertisement has no recognized qualifications
# heading, accept only explicitly requirement-like lines.
REQUIREMENT_CUE = re.compile(
    r"\b(?:you\s+(?:have|bring|need)|"
    r"experience\s+(?:with|in)|"
    r"proficien(?:t|cy)\s+(?:in|with)|"
    r"knowledge\s+of|must\s+have|required|"
    r"fluent\s+in|du\s+(?:bringst|hast|verfügst)|"
    r"sie\s+(?:bringen|verfügen)|"
    r"erfahrung\s+(?:mit|in)|"
    r"kenntnisse\s+(?:in|mit|von)|"
    r"sicherer\s+umgang\s+mit|vorausgesetzt|"
    r"mindestens\s+[ab]?[12]|"
    r"abgeschlossenes\s+studium)\b",
    re.IGNORECASE,
)

BENEFIT_CUE = re.compile(
    r"\b(?:we\s+offer|we\s+provide|your\s+benefits|"
    r"wir\s+bieten|du\s+bekommst|deine\s+benefits|"
    r"free\s+training|"
    r"weiterbildungsmöglichkeiten)\b",
    re.IGNORECASE,
)

# Detect only explicit CEFR language levels.
LANGUAGE = re.compile(
    r"\b(?P<language>"
    r"deutsch(?:kenntnisse)?|german|"
    r"englisch(?:kenntnisse)?|english)"
    r"\b[^\n.!?;]{0,28}?"
    r"\b(?P<level>[ABC][12])\b|"
    r"\b(?P<level_first>[ABC][12])\b"
    r"[^\n.!?;]{0,18}?"
    r"\b(?P<language_after>"
    r"deutsch(?:kenntnisse)?|german|"
    r"englisch(?:kenntnisse)?|english)\b",
    re.IGNORECASE,
)

# Possible field names in normalized source records.
DESCRIPTION_FIELDS = (
    "description",
    "job_description",
    "description_html",
    "jobDescription",
    "stellenbeschreibung",
    "beschreibung",
    "job_text",
    "text",
    "details",
    "job_details",
    "raw_description",
    "content",
)


# ============================================================
# 4. CONVERT HTML JOB DESCRIPTIONS INTO TEXT
# ============================================================

class _HTMLText(HTMLParser):
    """
    Convert HTML into readable text while preserving
    basic paragraph boundaries.

    Script and style contents are excluded.
    """

    def __init__(self) -> None:
        super().__init__(
            convert_charrefs=True
        )

        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(
        self,
        tag: str,
        attrs: list,
    ) -> None:

        if tag in {"script", "style"}:
            self.skip += 1

        if tag in {
            "p", "div", "li", "br",
            "h1", "h2", "h3", "h4",
            "ul", "ol",
        }:
            self.parts.append("\n")

    def handle_endtag(
        self,
        tag: str,
    ) -> None:

        if tag in {"script", "style"}:
            self.skip = max(
                0,
                self.skip - 1,
            )

        if tag in {
            "p", "div", "li",
            "h1", "h2", "h3", "h4",
        }:
            self.parts.append("\n")

    def handle_data(
        self,
        data: str,
    ) -> None:

        if not self.skip:
            self.parts.append(data)


def description_text(
    record: dict,
) -> str:
    """
    Extract a job description from supported fields.

    Job titles are deliberately excluded.
    """

    for field in DESCRIPTION_FIELDS:

        value = record.get(field)

        if isinstance(value, dict):

            value = next(
                (
                    value.get(key)

                    for key in (
                        "text",
                        "description",
                        "html",
                        "content",
                    )

                    if isinstance(
                        value.get(key),
                        str,
                    )
                ),
                None,
            )

        if not isinstance(value, str):
            continue

        if not value.strip():
            continue

        parser = _HTMLText()

        parser.feed(value)
        parser.close()

        lines = [
            re.sub(
                r"\s+",
                " ",
                item,
            ).strip()

            for item in "".join(
                parser.parts
            ).splitlines()
        ]

        return "\n".join(
            line
            for line in lines
            if line
        )

    return ""


# ============================================================
# 5. IDENTIFY REQUIREMENT LINES
# ============================================================

def requirement_lines(
    text: str,
) -> tuple[list[str], str]:
    """
    Prefer explicit qualification sections.

    If no qualification section is available, use
    conservative language-cue matching.

    Returns:
        requirement lines
        evidence extraction method
    """

    if not text:

        return [], "no_description"

    lines = [
        line.strip(
            " \t•-\u2022"
        )

        for line in text.splitlines()

        if line.strip()
    ]

    sections = []
    active = False

    for line in lines:

        start = START.match(line)

        if (
            start
            and len(line) < 130
        ):

            active = True

            remainder = start.group(
                1
            ).strip()

            if remainder:
                sections.append(
                    remainder
                )

            continue

        if active and STOP.match(line):

            active = False
            continue

        if active:

            sections.append(line)

    selected = [
        line

        for line in sections

        if not PREFERRED.search(line)

        and not BENEFIT_CUE.search(line)
    ]

    if selected:

        return (
            selected[:120],
            "requirements_section",
        )

    # Fallback: only explicit requirement statements.
    fallback = [
        line

        for line in lines

        if REQUIREMENT_CUE.search(line)

        and not PREFERRED.search(line)

        and not BENEFIT_CUE.search(line)
    ]

    return (
        fallback[:80],

        (
            "requirement_cues"
            if fallback
            else "no_requirements_evidence"
        ),
    )


# ============================================================
# 6. RECORD EXTRACTION EVIDENCE
# ============================================================

def _evidence(
    lines: list[str],
    patterns: dict[str, str],
) -> tuple[
    list[str],
    dict[str, list[str]],
]:
    """
    Match vocabulary against requirement lines.

    Every detected term retains a short source excerpt.

    This makes the extraction auditable.
    """

    found = defaultdict(list)

    for line in lines:

        for regex, term in patterns.items():

            if re.search(
                regex,
                line,
                re.IGNORECASE,
            ):

                if len(found[term]) < 2:

                    found[term].append(
                        line[:220]
                    )

    return (
        sorted(found),
        dict(found),
    )


# ============================================================
# 7. EXTRACT REQUIREMENTS FROM A JOB
# ============================================================

def extract_requirements(
    record: dict,
) -> dict:
    """
    Extract explicitly stated skills, domains and CEFR levels.

    IMPORTANT:
    Results are provisional.

    Rule-based detection may miss requirements or
    misclassify language within complex prose.

    Original source evidence is retained for review.
    """

    text = description_text(
        record
    )

    lines, basis = requirement_lines(
        text
    )

    skills, skill_evidence = _evidence(
        lines,
        SKILLS,
    )

    domains, domain_evidence = _evidence(
        lines,
        DOMAINS,
    )

    languages = {}
    language_evidence = {}

    cefr_order = (
        "A1 A2 B1 B2 C1 C2"
    ).split()

    for line in lines:

        for match in LANGUAGE.finditer(
            line
        ):

            language = (
                match.group("language")
                or match.group("language_after")
            )

            level = (
                match.group("level")
                or match.group("level_first")
            ).upper()

            if language.lower().startswith(
                ("deutsch", "german")
            ):

                label = "German"

            else:

                label = "English"

            old = languages.get(
                label
            )

            if (
                old is None
                or cefr_order.index(level)
                > cefr_order.index(old)
            ):

                languages[label] = level

                language_evidence[label] = (
                    line[:220]
                )

    return {
        "basis": basis,

        "has_description": bool(text),

        "requirement_lines": len(lines),

        "required_skills": skills,

        "required_domains": domains,

        "required_languages": languages,

        "evidence": {
            "skills": skill_evidence,

            "domains": domain_evidence,

            "languages": language_evidence,
        },

        "provisional": True,
    }


# ============================================================
# 8. MATCH JOBS TO THEIR ORIGINAL SOURCE RECORDS
# ============================================================

def _record_ids(
    record: dict,
) -> set[str]:
    """
    Extract source identifiers.

    We avoid fuzzy title-based joins because two different
    vacancies can have identical titles.
    """

    ids = set()

    for field in (
        "url",
        "job_url",
        "link",
        "source_url",
        "source_id",
        "id",
        "job_id",
        "refnr",
        "referenznummer",
    ):

        value = record.get(field)

        if isinstance(
            value,
            (str, int),
        ):

            if str(value).strip():

                ids.add(
                    str(value).strip()
                )

    return ids


# ============================================================
# 9. CONNECT EXISTING DISCOVERY AND CV SCORING
# ============================================================

def enrich_review_leads(
    raw_jobs: list[dict],
    discovery: dict,
    profile: dict,
    config: dict,
) -> list[dict]:
    """
    Join real source descriptions to reviewed vacancies.

    Call the existing local score_job() function.

    SAFETY:
    - Source records are matched only through identifiers.
    - Ambiguous matches are discarded.
    - All scores are preliminary.
    - No job is promoted to confirmed-open.
    """

    from src.services.match_scoring import (
        score_job,
    )

    index = defaultdict(list)

    for raw in raw_jobs:

        if not isinstance(raw, dict):
            continue

        for identifier in _record_ids(raw):

            index[identifier].append(
                raw
            )

    output = []

    for group in (
        "verification_queue",
        "ats_live_candidates",
    ):

        for lead in (
            discovery.get(group, [])
            or []
        ):

            if not isinstance(
                lead,
                dict,
            ):
                continue

            candidates = {}

            for identifier in _record_ids(
                lead
            ):

                for raw in index.get(
                    identifier,
                    [],
                ):

                    candidates[id(raw)] = raw

            # If several source records match, do not guess.
            raw = (
                next(
                    iter(
                        candidates.values()
                    )
                )

                if len(candidates) == 1

                else None
            )

            requirements = extract_requirements(
                raw or {}
            )

            scoring_input = {
                "required_skills": (
                    requirements["required_skills"]
                ),

                "required_domains": (
                    requirements["required_domains"]
                ),

                "required_languages": (
                    requirements["required_languages"]
                ),

                "geography": (
                    lead.get("geography")
                ),

                "seniority": (
                    lead.get("seniority")
                ),
            }

            # This uses the existing local scoring engine.
            # It does not call Ollama Cloud.
            matching = score_job(
                profile,
                scoring_input,
                config,
            )

            output.append({
                "title": lead.get("title"),

                "company": lead.get("company"),

                "url": lead.get("url"),

                "source": lead.get("source"),

                "verification_status": group,

                "source_record_linked": (
                    raw is not None
                ),

                "requirements": requirements,

                "matching": matching,

                "confirmed_open": False,
            })

    return output


# ============================================================
# 10. OFFLINE UNIT TESTS
# ============================================================

def run_self_test() -> None:
    """
    Verify extraction with fictional descriptions.

    No private professional profile is loaded.

    No job APIs or cloud LLMs are contacted.
    """

    # --------------------------------------------------------
    # TEST 1 — ENGLISH REQUIREMENTS
    # --------------------------------------------------------

    example = {
        "description": (
            "<h3>Your profile</h3>"
            "<ul>"
            "<li>Experience with Python and SQL</li>"
            "<li>Power BI and Reporting</li>"
            "<li>German B2 and English C1</li>"
            "</ul>"
            "<h3>Benefits</h3>"
            "<p>Training in Databricks</p>"
        )
    }

    result = extract_requirements(
        example
    )

    assert (
        result["basis"]
        == "requirements_section"
    )

    assert set(
        result["required_skills"]
    ) == {
        "Python",
        "SQL",
        "Power BI",
    }

    assert result["required_languages"] == {
        "German": "B2",
        "English": "C1",
    }

    assert (
        "Reporting"
        in result["required_domains"]
    )

    assert (
        "Databricks"
        not in result["required_skills"]
    )

    print(
        "PASS: HTML qualification sections "
        "and explicit CEFR detected"
    )

    # --------------------------------------------------------
    # TEST 2 — GERMAN REQUIREMENTS
    # --------------------------------------------------------

    german_example = {
        "description": (
            "<h3>Anforderungen</h3>"
            "<p>Erfahrung mit SQL.</p>"
            "<p>Python ist wünschenswert.</p>"
            "<h3>Wir bieten</h3>"
            "<p>Power BI Schulungen</p>"
        )
    }

    german_result = extract_requirements(
        german_example
    )

    assert (
        german_result["required_skills"]
        == ["SQL"]
    )

    print(
        "PASS: Optional skills "
        "and employer benefits excluded"
    )

    # --------------------------------------------------------
    # TEST 3 — NO INVENTED REQUIREMENTS
    # --------------------------------------------------------

    missing = extract_requirements({
        "title": "Senior Python Analyst",

        "description": (
            "We offer Python training "
            "and flexible benefits"
        ),
    })

    assert not missing[
        "required_skills"
    ]

    assert (
        missing["basis"]
        == "no_requirements_evidence"
    )

    print(
        "PASS: No job-title or benefits-based "
        "requirement invention"
    )

    # --------------------------------------------------------
    # TEST 4 — FALLBACK EXTRACTION
    # --------------------------------------------------------

    fallback = extract_requirements({
        "description": (
            "Experience with SQL and Tableau.\n"
            "Our office is in Berlin."
        ),
    })

    assert set(
        fallback["required_skills"]
    ) == {
        "SQL",
        "Tableau",
    }

    assert (
        fallback["basis"]
        == "requirement_cues"
    )

    print(
        "PASS: Cautious fallback for "
        "explicitly stated requirements"
    )

    # --------------------------------------------------------
    # TEST 5 — MISSING DESCRIPTIONS
    # --------------------------------------------------------

    assert (
        extract_requirements({})["basis"]
        == "no_description"
    )

    print(
        "PASS: Missing job description "
        "remains unknown"
    )

    print(
        "PASS: Step 47/48 extractor tests complete; "
        "no job API, CV, or LLM calls"
    )


# ============================================================
# 11. REAL JOB AND LOCAL CV PREVIEW
# ============================================================

def run_preview() -> None:
    """
    Run the existing job discovery pipeline.

    Then:
    - Link source descriptions to reviewed vacancies.
    - Extract provisional requirements.
    - Read the local professional profile.
    - Calculate provisional scores or ranges.
    - Print public job metadata and aggregate results.

    No CV information is uploaded.
    """

    from src.services.job_discovery import (
        collect_sources,
        prepare_jobs,
    )

    from src.services.match_scoring import (
        load_scoring_config,
        load_private_profile,
    )

    # Public job APIs are called here.
    raw = collect_sources()

    # Existing vacancy filters remain authoritative.
    discovery = prepare_jobs(raw)

    if discovery.get(
        "verified_open"
    ):

        raise ValueError(
            "Unsupported confirmed-open "
            "output blocked"
        )

    # Private professional evidence is read only locally.
    profile = load_private_profile()

    config = load_scoring_config()

    scored = enrich_review_leads(
        raw,
        discovery,
        profile,
        config,
    )

    print(
        "\n======= LOCAL REQUIREMENTS / MATCH PREVIEW ======="
    )

    print(
        "Public source records:",
        len(raw),
    )

    print(
        "Unverified review leads:",
        len(scored),
    )

    print(
        "Descriptions linked:",
        sum(
            item["source_record_linked"]
            and item["requirements"]["has_description"]

            for item in scored
        ),
    )

    print(
        "Leads with extracted skill evidence:",
        sum(
            bool(
                item["requirements"]["required_skills"]
            )

            for item in scored
        ),
    )

    print(
        "Leads with an exact preliminary score:",
        sum(
            item["matching"]["score"] is not None

            for item in scored
        ),
    )

    # Show a small sample without printing private CV data.
    for index, item in enumerate(
        scored[:8],
        1,
    ):

        requirements = item[
            "requirements"
        ]

        matching = item[
            "matching"
        ]

        print(
            f"\n{index}. "
            f"{item['title']} — {item['company']}"
        )

        print(
            "   Source:",
            item["url"],
        )

        print(
            "   Requirement evidence:",
            requirements["basis"],
            "| skills:",
            len(
                requirements["required_skills"]
            ),
            "| domains:",
            len(
                requirements["required_domains"]
            ),
        )

        if matching["score"] is not None:

            score_label = (
                f"{matching['score']}/100 "
                "(PRELIMINARY)"
            )

        else:

            score_label = (
                "possible range "
                f"{matching['possible_score_range']}"
            )

        print(
            "   Local CV match:",
            score_label,
        )

        print(
            "   Status: UNVERIFIED "
            "— NOT A RECOMMENDATION"
        )

    print(
        "\nNo CV information sent to cloud; "
        "no jobs approved for application."
    )


# ============================================================
# 12. COMMAND LINE
# ============================================================

def main() -> None:
    """
    --self-test:
        Run offline extraction tests.

    --preview:
        Fetch public listings and calculate
        local preliminary match evidence.
    """

    parser = argparse.ArgumentParser(
        description=(
            "JobRadar deterministic "
            "requirements extractor"
        )
    )

    actions = parser.add_mutually_exclusive_group(
        required=True
    )

    actions.add_argument(
        "--self-test",
        action="store_true",
    )

    actions.add_argument(
        "--preview",
        action="store_true",
    )

    args = parser.parse_args()

    if args.self_test:

        run_self_test()

    else:

        run_preview()


if __name__ == "__main__":
    main()