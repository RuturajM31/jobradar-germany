"""
JobRadar Germany — Bundesagentur Job Description Enrichment

THEORY
------
Search results often contain only job summaries.

For Bundesagentur für Arbeit listings, the detailed
advertisement is available through a separate endpoint.

This module:
1. Selects jobs already admitted to the review queue.
2. Finds the BA reference in the official source URL.
3. Retrieves the detailed advertisement.
4. Checks that the returned reference matches.
5. Supplies the description to our existing extractor.
6. Runs the existing CV scorer locally.

PRIVACY AND SAFETY
------------------
- Only public BA job references are sent to the BA API.
- The private CV/profile is never transmitted.
- No cloud LLM calls are made.
- No automatic applications.
- Unverified leads remain unverified.
"""

from __future__ import annotations

import argparse
import base64
import re

from collections import defaultdict
from typing import Callable
from urllib.parse import urlsplit


# ============================================================
# 1. PUBLIC API CONFIGURATION
# ============================================================

BA_DETAILS_BASE = (
    "https://rest.arbeitsagentur.de/"
    "jobboerse/jobsuche-service/pc/v4/jobdetails/"
)

# Public Jobsuche API client identifier.
BA_API_KEY = "jobboerse-jobsuche"

# Official BA job-detail URL reference format.
REFERENCE_PATH = re.compile(
    r"/jobsuche/jobdetail/"
    r"([A-Za-z0-9][A-Za-z0-9_-]{5,119})/?"
)

# The documented detail API can use either field name.
DESCRIPTION_KEYS = (
    "stellenangebotsBeschreibung",
    "stellenbeschreibung",
)


# ============================================================
# 2. EXTRACT A TRUSTED BA REFERENCE
# ============================================================

def ba_reference(
    lead: dict,
) -> str | None:
    """
    Extract the reference from a genuine BA jobdetail URL.

    Example:
        https://www.arbeitsagentur.de/jobsuche/jobdetail/
        12811-2349538-S

    Returns:
        12811-2349538-S

    We do not trust arbitrary external URLs.
    """

    url = lead.get("url")

    if not isinstance(url, str):
        return None

    try:
        parts = urlsplit(url)

    except ValueError:
        return None

    if (
        parts.scheme != "https"
        or parts.hostname not in {
            "www.arbeitsagentur.de",
            "arbeitsagentur.de",
        }
    ):
        return None

    match = REFERENCE_PATH.fullmatch(
        parts.path
    )

    return (
        match.group(1)
        if match
        else None
    )


# ============================================================
# 3. RETRIEVE THE BA JOB DESCRIPTION
# ============================================================

def get_ba_detail(
    reference: str,
    *,
    http_get: Callable | None = None,
    timeout: int = 10,
) -> dict:
    """
    Fetch a public BA job description.

    API contract:
        GET /pc/v4/jobdetails/{base64(refnr)}

    Header:
        X-API-Key: jobboerse-jobsuche

    The response reference must match the requested
    job. Otherwise, the description is rejected.

    HTTPS certificate verification remains enabled.
    """

    if (
        not isinstance(reference, str)
        or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_-]{5,119}",
            reference,
        )
    ):
        return {
            "status": "invalid_reference",
            "description": "",
        }

    if http_get is None:

        import requests

        http_get = requests.get

    # Encode the public reference as required by BA.
    encoded = base64.b64encode(
        reference.encode("ascii")
    ).decode("ascii")

    url = (
        BA_DETAILS_BASE
        + encoded
    )

    try:

        response = http_get(
            url,

            headers={
                "X-API-Key": BA_API_KEY,
                "Accept": "application/json",
            },

            timeout=timeout,
        )

        if response.status_code != 200:

            return {
                "status": (
                    f"http_{response.status_code}"
                ),
                "description": "",
            }

        payload = response.json()

    except Exception:

        # Network failures must never become evidence.
        return {
            "status": "request_or_json_error",
            "description": "",
        }

    if not isinstance(payload, dict):

        return {
            "status": "invalid_payload",
            "description": "",
        }

    # Check that the API returned the requested posting.
    returned_ref = (
        payload.get("referenznummer")
        or payload.get("refnr")
    )

    if (
        not isinstance(returned_ref, str)
        or returned_ref != reference
    ):

        return {
            "status": "reference_unconfirmed",
            "description": "",
        }

    # Select a substantial description from the API.
    description = next(
        (
            payload[key]

            for key in DESCRIPTION_KEYS

            if (
                isinstance(
                    payload.get(key),
                    str,
                )

                and len(
                    payload[key].strip()
                ) >= 40
            )
        ),

        "",
    )

    if not description:

        return {
            "status": "description_missing",
            "description": "",
        }

    return {
        "status": "ok",
        "description": description,
    }


# ============================================================
# 4. EXTRACT EXISTING SOURCE IDENTIFIERS
# ============================================================

def _raw_identifiers(
    record: dict,
) -> set[str]:
    """
    Collect exact identifiers for source-record matching.

    We deliberately avoid title-based fuzzy matching.

    Different vacancies can have the same title.
    """

    identifiers = set()

    for key in (
        "url",
        "source_id",
        "id",
        "refnr",
        "referenznummer",
    ):

        value = record.get(key)

        if (
            isinstance(value, (str, int))
            and str(value).strip()
        ):

            identifiers.add(
                str(value).strip()
            )

    return identifiers


# ============================================================
# 5. BUILD DESCRIPTION RECORDS FOR REVIEW LEADS
# ============================================================

def build_description_records(
    raw_jobs: list[dict],
    discovery: dict,
    *,
    max_ba_requests: int = 12,
    http_get: Callable | None = None,
) -> tuple[list[dict], dict]:
    """
    Build one canonical description record per review lead.

    Processing order:

    1. Search existing source records for a description.
    2. If missing and it is a BA listing, request details.
    3. Match descriptions using exact identifiers.
    4. Preserve unknown descriptions as empty strings.

    Only review leads are eligible.

    Excluded stale, foreign, or student jobs
    are not enriched for recommendations.

    The returned records can be passed to:
        job_requirements.enrich_review_leads()
    """

    from src.services.job_requirements import (
        description_text,
    )

    if (
        not isinstance(max_ba_requests, int)
        or not 0 <= max_ba_requests <= 30
    ):

        raise ValueError(
            "max_ba_requests must be between 0 and 30."
        )

    # --------------------------------------------------------
    # INDEX THE ORIGINAL SOURCE RECORDS
    # --------------------------------------------------------

    index = defaultdict(list)

    for raw in raw_jobs:

        if not isinstance(raw, dict):
            continue

        for identifier in _raw_identifiers(raw):

            index[identifier].append(raw)

    # --------------------------------------------------------
    # INITIALIZE STATISTICS
    # --------------------------------------------------------

    stats = {
        "review_leads": 0,
        "source_descriptions": 0,
        "ba_requests": 0,
        "ba_descriptions": 0,
        "ba_unsuccessful": 0,
        "ambiguous_raw_matches": 0,
        "no_description": 0,
    }

    canonical = []

    visited_urls = set()

    # Avoid downloading the same BA reference twice.
    detail_cache = {}

    # --------------------------------------------------------
    # PROCESS ONLY REVIEW LEADS
    # --------------------------------------------------------

    for group in (
        "verification_queue",
        "ats_live_candidates",
    ):

        for lead in (
            discovery.get(group, [])
            or []
        ):

            if not isinstance(lead, dict):
                continue

            url = lead.get("url")

            if (
                not isinstance(url, str)
                or not url.strip()
                or url in visited_urls
            ):
                continue

            visited_urls.add(url)

            stats["review_leads"] += 1

            # -----------------------------------------------
            # FIND THE ORIGINAL SOURCE RECORD
            # -----------------------------------------------

            matches = {}

            for identifier in _raw_identifiers(lead):

                for raw in index.get(
                    identifier,
                    [],
                ):

                    matches[id(raw)] = raw

            if len(matches) > 1:

                stats[
                    "ambiguous_raw_matches"
                ] += 1

            # Never choose an arbitrary source record
            # when multiple different records match.
            raw = (
                next(iter(matches.values()))

                if len(matches) == 1

                else {}
            )

            description = description_text(
                raw
            )

            if description:

                stats[
                    "source_descriptions"
                ] += 1

            # -----------------------------------------------
            # RETRIEVE MISSING BA DETAILS
            # -----------------------------------------------

            reference = ba_reference(
                lead
            )

            if (
                not description
                and reference
            ):

                if (
                    reference not in detail_cache
                    and stats["ba_requests"]
                    < max_ba_requests
                ):

                    stats["ba_requests"] += 1

                    detail_cache[reference] = (
                        get_ba_detail(
                            reference,
                            http_get=http_get,
                        )
                    )

                detail = detail_cache.get(
                    reference,
                    {},
                )

                if (
                    detail.get("status")
                    == "ok"
                ):

                    description = detail[
                        "description"
                    ]

                    stats[
                        "ba_descriptions"
                    ] += 1

                elif detail:

                    stats[
                        "ba_unsuccessful"
                    ] += 1

            # -----------------------------------------------
            # PRESERVE MISSING INFORMATION
            # -----------------------------------------------

            if not description:

                stats[
                    "no_description"
                ] += 1

            # Exactly one canonical description record
            # per lead URL prevents duplicate joins.
            #
            # No professional-profile fields are included.
            canonical.append({
                "url": url,
                "description": description,
            })

    return canonical, stats


# ============================================================
# 6. LIVE PREVIEW: REQUIREMENTS + LOCAL SCORING
# ============================================================

def run_preview(
    max_ba_requests: int,
) -> None:
    """
    Run the integrated local preview.

    Workflow:

    Public job APIs
        ↓
    Existing discovery and quality filters
        ↓
    BA detailed descriptions
        ↓
    Existing requirements extractor
        ↓
    Existing local CV scorer
        ↓
    Preliminary results only

    The profile is never uploaded.
    """

    from src.services.job_discovery import (
        collect_sources,
        prepare_jobs,
    )

    from src.services.job_requirements import (
        enrich_review_leads,
    )

    from src.services.match_scoring import (
        load_private_profile,
        load_scoring_config,
    )

    # --------------------------------------------------------
    # LIVE DISCOVERY
    # --------------------------------------------------------

    raw = collect_sources()

    results = prepare_jobs(
        raw
    )

    if results.get("verified_open"):

        raise ValueError(
            "Unsupported confirmed-open output. "
            "Review blocked."
        )

    # --------------------------------------------------------
    # FETCH MISSING BA DESCRIPTIONS
    # --------------------------------------------------------

    canonical, stats = (
        build_description_records(
            raw,
            results,

            max_ba_requests=max_ba_requests,
        )
    )

    # --------------------------------------------------------
    # LOCAL CV MATCHING
    # --------------------------------------------------------

    scored = enrich_review_leads(
        canonical,
        results,

        load_private_profile(),
        load_scoring_config(),
    )

    # --------------------------------------------------------
    # RESULTS
    # --------------------------------------------------------

    print(
        "\n========== BA DETAILS + LOCAL MATCH PREVIEW =========="
    )

    print(
        "Public source records:",
        len(raw),
    )

    for key, value in stats.items():

        print(
            key.replace(
                "_",
                " ",
            ).capitalize() + ":",

            value,
        )

    print(
        "Leads with extracted skills:",

        sum(
            bool(
                item["requirements"][
                    "required_skills"
                ]
            )

            for item in scored
        ),
    )

    print(
        "Exact preliminary scores:",

        sum(
            item["matching"]["score"]
            is not None

            for item in scored
        ),
    )

    # Show up to eight leads.
    for number, item in enumerate(
        scored[:8],
        start=1,
    ):

        requirements = item[
            "requirements"
        ]

        matching = item[
            "matching"
        ]

        print(
            f"\n{number}. "
            f"{item['title']} — {item['company']}"
        )

        print(
            "   Description:",

            (
                "present"
                if requirements["has_description"]
                else "missing"
            ),
        )

        print(
            "   Evidence:",

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

        if matching["score"] is None:

            print(
                "   Preliminary possible score range:",

                matching[
                    "possible_score_range"
                ],
            )

        else:

            print(
                "   Preliminary score:",

                matching["score"],
            )

        print(
            "   Status: UNVERIFIED "
            "— NOT A RECOMMENDATION"
        )

    print(
        "\nNo CV uploaded, no employer contacted, "
        "no jobs approved."
    )


# ============================================================
# 7. DETERMINISTIC OFFLINE TESTS
# ============================================================

def run_self_test() -> None:
    """
    Test the BA detail integration using fictional responses.

    No real BA API requests are made.

    No actual CV or LLM requests are made.
    """

    reference = "12811-2349538-S"

    lead_url = (
        "https://www.arbeitsagentur.de/"
        "jobsuche/jobdetail/"
        + reference
    )

    # --------------------------------------------------------
    # TEST 1 — REFERENCE VALIDATION
    # --------------------------------------------------------

    assert ba_reference({
        "url": lead_url
    }) == reference

    assert ba_reference({
        "url": (
            "https://example.org/"
            "jobsuche/jobdetail/"
            + reference
        )
    }) is None

    print(
        "PASS: Only genuine BA jobdetail links "
        "supply references"
    )

    # --------------------------------------------------------
    # TEST 2 — MOCKED DETAIL REQUEST
    # --------------------------------------------------------

    calls = []

    class FakeResponse:

        status_code = 200

        def json(self):

            return {
                "referenznummer": reference,

                "stellenangebotsBeschreibung": (
                    "<h3>Anforderungen</h3>"
                    "<p>Erfahrung mit SQL "
                    "und Power BI sowie Reporting "
                    "erforderlich.</p>"
                ),
            }

    def fake_get(
        url,
        *,
        headers,
        timeout,
    ):

        calls.append(
            (url, headers, timeout)
        )

        return FakeResponse()

    detail = get_ba_detail(
        reference,
        http_get=fake_get,
    )

    assert detail["status"] == "ok"

    assert calls[0][0].endswith(
        base64.b64encode(
            reference.encode()
        ).decode()
    )

    assert (
        calls[0][1]["X-API-Key"]
        == BA_API_KEY
    )

    print(
        "PASS: BA Base64 detail request "
        "and description parsing"
    )

    # --------------------------------------------------------
    # TEST 3 — WRONG REFERENCES
    # --------------------------------------------------------

    def wrong_get(
        url,
        *,
        headers,
        timeout,
    ):

        class Wrong(FakeResponse):

            def json(self):

                return {
                    "referenznummer": "99999-OTHER-S",

                    "stellenangebotsBeschreibung": (
                        "A" * 100
                    ),
                }

        return Wrong()

    assert (
        get_ba_detail(
            reference,
            http_get=wrong_get,
        )["status"]
        == "reference_unconfirmed"
    )

    assert (
        get_ba_detail(
            "invalid ref",
            http_get=fake_get,
        )["status"]
        == "invalid_reference"
    )

    print(
        "PASS: Wrong reference and "
        "malformed input rejected"
    )

    # --------------------------------------------------------
    # TEST 4 — CANONICAL SOURCE RECORDS
    # --------------------------------------------------------

    raw = [
        {
            "url": lead_url,
            "description": "",
        },

        {
            "url": (
                "https://www.arbeitnow.com/"
                "fictional-role"
            ),

            "description": (
                "<h3>Your profile</h3>"
                "<p>Experience with "
                "Python and SQL.</p>"
            ),
        },
    ]

    discovery = {
        "verification_queue": [
            {
                "url": lead_url,
                "title": "Data Analyst",
            },

            {
                "url": (
                    "https://www.arbeitnow.com/"
                    "fictional-role"
                ),
                "title": "BI Analyst",
            },
        ],

        "ats_live_candidates": [],
    }

    records, stats = (
        build_description_records(
            raw,
            discovery,

            max_ba_requests=1,

            http_get=fake_get,
        )
    )

    assert len(records) == 2

    assert stats["ba_requests"] == 1

    assert stats["ba_descriptions"] == 1

    assert stats["source_descriptions"] == 1

    assert records[0]["url"] == lead_url

    assert (
        "Power BI"
        in records[0]["description"]
    )

    assert (
        "Python"
        in records[1]["description"]
    )

    print(
        "PASS: Review-only hydration preserves "
        "one canonical record per lead"
    )

    print(
        "PASS: Tests completed without "
        "real API, CV or LLM calls"
    )


# ============================================================
# 8. COMMAND-LINE INTERFACE
# ============================================================

def main() -> None:
    """
    --self-test:
        Run offline regression tests.

    --preview:
        Run public job discovery, fetch missing BA
        descriptions and calculate local match evidence.

    --max-ba-details:
        Limit detail requests to avoid unnecessary
        traffic and long search times.
    """

    parser = argparse.ArgumentParser(
        description=(
            "JobRadar BA description enrichment"
        )
    )

    group = (
        parser.add_mutually_exclusive_group(
            required=True
        )
    )

    group.add_argument(
        "--self-test",
        action="store_true",
    )

    group.add_argument(
        "--preview",
        action="store_true",
    )

    parser.add_argument(
        "--max-ba-details",
        type=int,
        default=12,

        help=(
            "Maximum BA detail requests "
            "(default 12; maximum 30)"
        ),
    )

    args = parser.parse_args()

    if args.self_test:

        run_self_test()

    else:

        run_preview(
            args.max_ba_details
        )


if __name__ == "__main__":
    main()