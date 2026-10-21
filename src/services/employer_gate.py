"""
JobRadar Germany — Independent Employer ATS Evidence Gate

Checks public vacancies against official employer ATS APIs.

Supported:
    Ashby
    Lever
    Greenhouse

IMPORTANT:
A publicly listed vacancy is not proof that its application
form currently accepts submissions.

This module never:
- Uploads the user's CV
- Calls an LLM
- Submits applications
- Fabricates confirmed-open recommendations
"""

from __future__ import annotations

import argparse
import re

from datetime import date, datetime, timezone
from difflib import SequenceMatcher
from urllib.parse import quote, urlsplit


# ============================================================
# CONFIGURATION
# ============================================================

ATS_DOMAINS = {
    "ashby": "jobs.ashbyhq.com",

    "greenhouse": {
        "boards.greenhouse.io",
        "job-boards.greenhouse.io",
    },

    "lever": {
        "jobs.lever.co",
        "jobs.eu.lever.co",
    },
}

MAX_AGE_DAYS = 30


# ============================================================
# 1. IDENTIFY SUPPORTED EMPLOYER ATS LINKS
# ============================================================

def ats_identity(value: object) -> dict | None:
    """
    Identify an official ATS link.

    SECURITY:
    Reject arbitrary hosts and malformed identifiers.

    Only three explicitly supported ATS providers
    are accepted.
    """

    if not isinstance(value, str):
        return None

    try:

        p = urlsplit(
            value.strip()
        )

        if (
            p.scheme != "https"
            or p.username
            or p.password
            or p.port
        ):
            return None

    except ValueError:
        return None

    parts = [
        item
        for item in p.path.split("/")
        if item
    ]

    if (
        p.hostname == ATS_DOMAINS["ashby"]
        and len(parts) >= 2
    ):

        kind = "ashby"
        board = parts[0]
        job = parts[1]

    elif (
        p.hostname in ATS_DOMAINS["greenhouse"]
        and len(parts) >= 3
        and parts[1] == "jobs"
    ):

        kind = "greenhouse"
        board = parts[0]
        job = parts[2]

    elif (
        p.hostname in ATS_DOMAINS["lever"]
        and len(parts) >= 2
    ):

        kind = "lever"
        board = parts[0]
        job = parts[1]

    else:

        return None

    for token in (board, job):

        if not re.fullmatch(
            r"[A-Za-z0-9_-]{2,100}",
            token,
        ):
            return None

    return {
        "ats": kind,
        "board": board,
        "job_id": job,
        "hostname": p.hostname,
    }


# ============================================================
# 2. VALIDATE OFFICIAL APPLICATION LINKS
# ============================================================

def official_link(
    value: object,
    kind: str,
    board: str,
) -> str | None:
    """
    Accept only platform-hosted application links.

    A valid application URL indicates an official
    destination, not successful application acceptance.
    """

    if not isinstance(value, str):
        return None

    try:

        p = urlsplit(value)

        if (
            p.scheme != "https"
            or p.username
            or p.password
            or p.port
        ):
            return None

    except ValueError:
        return None

    if kind == "ashby":

        allowed = {
            ATS_DOMAINS["ashby"]
        }

    elif kind == "greenhouse":

        allowed = ATS_DOMAINS["greenhouse"]

    elif kind == "lever":

        allowed = ATS_DOMAINS["lever"]

    else:

        return None

    if (
        p.hostname not in allowed
        or any(char.isspace() for char in value)
    ):
        return None

    if not p.path.startswith(
        "/" + board + "/"
    ):
        return None

    return value


# ============================================================
# 3. COMPARE JOB TITLES
# ============================================================

def title_corresponds(
    source: object,
    ats_title: object,
) -> bool:
    """
    Verify that the ATS posting title matches the source.

    The comparison permits small differences such as
    removing the German (m/w/d) designation.

    It does not use company-independent keyword matching.
    """

    if (
        not isinstance(source, str)
        or not isinstance(ats_title, str)
    ):
        return False

    def normalize(value: str) -> str:

        value = re.sub(
            r"\b(?:m\s*/\s*w\s*/\s*d|"
            r"w\s*/\s*m\s*/\s*d|"
            r"f\s*/\s*m\s*/\s*d)\b",

            " ",

            value.casefold(),
        )

        return " ".join(
            re.findall(
                r"[a-z0-9äöüß]+",
                value,
            )
        )

    first = normalize(source)
    second = normalize(ats_title)

    if not first or not second:
        return False

    if first == second:
        return True

    return (
        SequenceMatcher(
            None,
            first,
            second,
        ).ratio()
        >= 0.86
    )


# ============================================================
# 4. HANDLE PUBLICATION DATES
# ============================================================

def published_age(
    raw: object,
    today: date,
) -> int | None:
    """
    Calculate days since an explicit ATS publication date.

    IMPORTANT:
    Never substitute updated_at for published_at.

    Last-updated timestamps do not establish
    when a job was originally advertised.
    """

    if (
        not isinstance(raw, str)
        or not raw.strip()
    ):
        return None

    try:

        posted = datetime.fromisoformat(
            raw.replace(
                "Z",
                "+00:00",
            )
        ).date()

        days = (
            today - posted
        ).days

        return (
            days
            if days >= 0
            else None
        )

    except ValueError:

        return None


# ============================================================
# 5. STANDARD VERIFICATION RESULT
# ============================================================

def result(
    status: str,
    *,
    kind: str | None = None,
    url: str | None = None,
    reasons: list[str] | None = None,
    **extra,
) -> dict:
    """
    Return a consistent employer-verification result.

    A successful ATS listing may become ready for
    manual confirmation, but never automatically
    become an approved recommendation.
    """

    return {
        "decision": status,

        "ats": kind,

        "official_apply_url": url,

        "confirmed_open": False,

        "approved_recommendation": False,

        "reasons": reasons or [],

        **extra,
    }


# ============================================================
# 6. VERIFY ONE EMPLOYER ATS POSTING
# ============================================================

def verify_ats_posting(
    lead: dict,
    *,
    http_get=None,
    today: date | None = None,
    timeout: int = 10,
) -> dict:
    """
    Check an exact employer ATS posting.

    Supported outcomes:

        ready_for_manual_check
        review
        excluded

    Even ready_for_manual_check requires manual
    confirmation of application acceptance.

    No applications are submitted.
    """

    candidates = [
        lead.get("employer_ats_url"),
        lead.get("application_url"),
        lead.get("url"),
    ]

    identity = next(
        (
            found

            for value in candidates

            if (
                found := ats_identity(value)
            )
        ),
        None,
    )

    if not identity:

        return result(
            "review",

            reasons=[
                "No supported direct employer ATS URL"
            ],
        )

    kind = identity["ats"]
    board = identity["board"]
    job_id = identity["job_id"]

    if (
        not isinstance(
            lead.get("title"),
            str,
        )
        or not lead["title"].strip()
    ):

        return result(
            "review",

            kind=kind,

            reasons=[
                "Source job title missing"
            ],
        )

    # --------------------------------------------------------
    # BUILD OFFICIAL PROVIDER API ENDPOINT
    # --------------------------------------------------------

    if kind == "ashby":

        endpoint = (
            "https://api.ashbyhq.com/"
            "posting-api/job-board/"
            + quote(board)
        )

    elif kind == "lever":

        hostname = (
            "api.eu.lever.co"

            if (
                identity["hostname"]
                == "jobs.eu.lever.co"
            )

            else "api.lever.co"
        )

        endpoint = (
            f"https://{hostname}/v0/postings/"
            f"{quote(board)}/{quote(job_id)}"
        )

    else:

        endpoint = (
            "https://boards-api.greenhouse.io/"
            "v1/boards/"
            f"{quote(board)}/jobs/{quote(job_id)}"
        )

    # --------------------------------------------------------
    # REQUEST THE OFFICIAL PUBLIC POSTING
    # --------------------------------------------------------

    if http_get is None:

        import requests

        http_get = requests.get

    try:

        response = http_get(
            endpoint,

            headers={
                "Accept": "application/json"
            },

            timeout=timeout,

            allow_redirects=False,
        )

        if response.status_code != 200:

            return result(
                "review",

                kind=kind,

                reasons=[
                    "Employer ATS HTTP status: "
                    f"{response.status_code}"
                ],
            )

        payload = response.json()

    except Exception:

        return result(
            "review",

            kind=kind,

            reasons=[
                "Employer ATS response unavailable "
                "or invalid"
            ],
        )

    # --------------------------------------------------------
    # ASHBY — STRUCTURED PUBLICATION AND EMPLOYMENT
    # --------------------------------------------------------

    if kind == "ashby":

        if (
            not isinstance(payload, dict)
            or not isinstance(
                payload.get("jobs"),
                list,
            )
        ):

            return result(
                "review",

                kind=kind,

                reasons=[
                    "Unexpected Ashby API response"
                ],
            )

        matching = []

        for job in payload["jobs"]:

            if not isinstance(job, dict):
                continue

            posted_identity = ats_identity(
                job.get("jobUrl")
            )

            if (
                posted_identity
                and posted_identity["ats"] == "ashby"
                and posted_identity["board"].casefold()
                == board.casefold()
                and posted_identity["job_id"] == job_id
            ):

                matching.append(job)

        if len(matching) != 1:

            return result(
                "review",

                kind=kind,

                reasons=[
                    "Exact posting absent or ambiguous "
                    "on employer board"
                ],
            )

        posting = matching[0]

        if posting.get("isListed") is not True:

            return result(
                "review",

                kind=kind,

                reasons=[
                    "Posting is not publicly listed"
                ],
            )

        title = posting.get("title")

        employment = posting.get(
            "employmentType"
        )

        age = published_age(
            posting.get("publishedAt"),

            today
            or datetime.now(
                timezone.utc
            ).date(),
        )

        apply_url = official_link(
            posting.get("applyUrl"),
            kind,
            board,
        )

    # --------------------------------------------------------
    # LEVER — PUBLIC POSTING AND COMMITMENT
    # --------------------------------------------------------

    elif kind == "lever":

        if (
            not isinstance(payload, dict)
            or str(payload.get("id")) != job_id
        ):

            return result(
                "review",

                kind=kind,

                reasons=[
                    "Lever posting ID mismatch "
                    "or missing"
                ],
            )

        posting = payload

        title = posting.get("text")

        categories = (
            posting.get("categories")
            or {}
        )

        employment = (
            categories.get("commitment")

            if isinstance(categories, dict)

            else None
        )

        # Do not infer original publication dates
        # from undocumented timestamps.
        age = None

        apply_url = official_link(
            posting.get("applyUrl"),
            kind,
            board,
        )

        hosted = ats_identity(
            posting.get("hostedUrl")
        )

        if (
            not hosted
            or hosted["board"] != board
            or hosted["job_id"] != job_id
        ):

            return result(
                "review",

                kind=kind,

                reasons=[
                    "Lever official hosted URL mismatch"
                ],
            )

    # --------------------------------------------------------
    # GREENHOUSE — PUBLIC POSTING DETAILS
    # --------------------------------------------------------

    else:

        if (
            not isinstance(payload, dict)
            or str(payload.get("id")) != job_id
        ):

            return result(
                "review",

                kind=kind,

                reasons=[
                    "Greenhouse posting ID mismatch "
                    "or missing"
                ],
            )

        posting = payload

        title = posting.get("title")

        # updated_at does not prove original publication.
        age = None

        # Do not guess full-time status from a title.
        employment = None

        apply_url = official_link(
            posting.get("absolute_url"),
            kind,
            board,
        )

    # ========================================================
    # 7. APPLY FINAL EVIDENCE RULES
    # ========================================================

    if not title_corresponds(
        lead["title"],
        title,
    ):

        return result(
            "review",

            kind=kind,

            reasons=[
                "ATS title differs from source title"
            ],
        )

    reasons = []

    if not apply_url:

        reasons.append(
            "Official ATS application link "
            "not confirmed"
        )

    fulltime = (
        str(employment or "")
        .strip()
        .casefold()
        .replace("-", "")
        .replace(" ", "")
    )

    if fulltime in {
        "parttime",
        "intern",
        "contract",
        "temporary",
        "freelance",
    }:

        return result(
            "excluded",

            kind=kind,

            reasons=[
                "Employer lists a non-full-time role"
            ],

            published_age_days=age,
        )

    if fulltime != "fulltime":

        reasons.append(
            "Structured full-time employment "
            "not confirmed"
        )

    if age is None:

        reasons.append(
            "Recent publication date "
            "not established"
        )

    elif age > MAX_AGE_DAYS:

        return result(
            "excluded",

            kind=kind,

            reasons=[
                f"Published {age} days ago "
                f"(>{MAX_AGE_DAYS})"
            ],

            published_age_days=age,
        )

    if reasons:

        return result(
            "review",

            kind=kind,

            url=apply_url,

            reasons=reasons,

            published_age_days=age,
        )

    # The posting passed the available ATS checks.
    #
    # Application acceptance must still be
    # confirmed manually.
    return result(
        "ready_for_manual_check",

        kind=kind,

        url=apply_url,

        reasons=[
            "Employer-listed, explicitly full-time "
            "and recently published; manually "
            "confirm application acceptance"
        ],

        published_age_days=age,
    )


# ============================================================
# 8. CHECK A BOUNDED NUMBER OF DISCOVERED JOBS
# ============================================================

def check_discovery(
    discovery: dict,
    *,
    max_checks: int = 8,
    http_get=None,
    today: date | None = None,
) -> dict:
    """
    Check only review-eligible jobs.

    Excluded advertisements are not reconsidered.

    Limit employer API requests to a manageable number.
    """

    if discovery.get("verified_open"):

        raise ValueError(
            "Unsupported verified_open claims "
            "must not be accepted"
        )

    if (
        not isinstance(max_checks, int)
        or not 0 <= max_checks <= 30
    ):

        raise ValueError(
            "max_checks must be 0..30"
        )

    tested = []
    skipped = 0
    seen = set()

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

            key = lead.get("url")

            if (
                not isinstance(key, str)
                or key in seen
            ):
                continue

            seen.add(key)

            eligible_ats = any(
                ats_identity(
                    lead.get(field)
                )

                for field in (
                    "employer_ats_url",
                    "application_url",
                    "url",
                )
            )

            if not eligible_ats:

                skipped += 1
                continue

            if len(tested) >= max_checks:

                skipped += 1
                continue

            verdict = verify_ats_posting(
                lead,

                http_get=http_get,

                today=today,
            )

            tested.append({
                "title": lead.get("title"),

                "source_url": key,

                **verdict,
            })

    return {
        "checked": tested,

        "without_check": skipped,

        "confirmed_open": [],

        "ready_for_manual_check": sum(
            item["decision"]
            == "ready_for_manual_check"

            for item in tested
        ),

        "review": sum(
            item["decision"] == "review"

            for item in tested
        ),

        "excluded": sum(
            item["decision"] == "excluded"

            for item in tested
        ),
    }


# ============================================================
# 9. OFFLINE SELF-TESTS
# ============================================================

def run_self_test() -> None:
    """
    Test the official ATS verification contract.

    Uses fictional postings and mocked HTTP responses.

    No real APIs, CV files or LLMs are accessed.
    """

    today = date(
        2026,
        10,
        21,
    )

    base = (
        "https://jobs.ashbyhq.com/"
        "example/role123"
    )

    lead = {
        "title": "Senior Data Analyst (m/w/d)",

        "url": "https://source.example/1",

        "employer_ats_url": base,
    }

    fake_job = {
        "title": "Senior Data Analyst",

        "jobUrl": base,

        "applyUrl": base + "/application",

        "isListed": True,

        "employmentType": "FullTime",

        "publishedAt": "2026-10-15T08:00:00Z",
    }

    class FakeResponse:

        status_code = 200

        def __init__(self, payload):

            self.payload = payload

        def json(self):

            return self.payload

    def mock(payload):

        def get(url, **kwargs):

            assert url == (
                "https://api.ashbyhq.com/"
                "posting-api/job-board/example"
            )

            assert kwargs["timeout"] == 10

            assert (
                kwargs["allow_redirects"]
                is False
            )

            return FakeResponse(payload)

        return get

    # --------------------------------------------------------
    # TEST 1 — OFFICIAL DOMAINS ONLY
    # --------------------------------------------------------

    assert ats_identity(base) == {
        "ats": "ashby",
        "board": "example",
        "job_id": "role123",
        "hostname": "jobs.ashbyhq.com",
    }

    assert ats_identity(
        "https://evil.example/jobs/xyz"
    ) is None

    assert official_link(
        "javascript:alert(1)",
        "ashby",
        "example",
    ) is None

    print(
        "PASS: Whitelisted ATS URLs only"
    )

    # --------------------------------------------------------
    # TEST 2 — RECENT FULL-TIME POSTING
    # --------------------------------------------------------

    valid = verify_ats_posting(
        lead,

        http_get=mock({
            "jobs": [fake_job]
        }),

        today=today,
    )

    assert (
        valid["decision"]
        == "ready_for_manual_check"
    )

    assert (
        valid["confirmed_open"]
        is False
    )

    assert (
        valid["published_age_days"]
        == 6
    )

    print(
        "PASS: Recent full-time ATS listing "
        "still requires final manual acceptance check"
    )

    # --------------------------------------------------------
    # TEST 3 — EXCLUDE UNSUITABLE OR MISMATCHED JOBS
    # --------------------------------------------------------

    unlisted = verify_ats_posting(
        lead,

        http_get=mock({
            "jobs": [
                {
                    **fake_job,
                    "isListed": False,
                }
            ]
        }),

        today=today,
    )

    assert unlisted["decision"] == "review"

    parttime = verify_ats_posting(
        lead,

        http_get=mock({
            "jobs": [
                {
                    **fake_job,
                    "employmentType": "PartTime",
                }
            ]
        }),

        today=today,
    )

    assert parttime["decision"] == "excluded"

    stale = verify_ats_posting(
        lead,

        http_get=mock({
            "jobs": [
                {
                    **fake_job,
                    "publishedAt": "2026-08-01",
                }
            ]
        }),

        today=today,
    )

    assert stale["decision"] == "excluded"

    mismatch = verify_ats_posting(
        lead,

        http_get=mock({
            "jobs": [
                {
                    **fake_job,
                    "title": "Java Developer",
                }
            ]
        }),

        today=today,
    )

    assert mismatch["decision"] == "review"

    print(
        "PASS: Unlisted, non-full-time, stale "
        "and mismatched records never promoted"
    )

    # --------------------------------------------------------
    # TEST 4 — GREENHOUSE DATE EVIDENCE
    # --------------------------------------------------------

    greenhouse = {
        "title": "Data Analyst",

        "employer_ats_url": (
            "https://boards.greenhouse.io/"
            "example/jobs/12345"
        ),
    }

    def greenhouse_get(url, **kwargs):

        assert url == (
            "https://boards-api.greenhouse.io/"
            "v1/boards/example/jobs/12345"
        )

        return FakeResponse({
            "id": 12345,

            "title": "Data Analyst",

            "absolute_url": (
                "https://boards.greenhouse.io/"
                "example/jobs/12345"
            ),

            "updated_at": "2026-10-20",
        })

    checked = verify_ats_posting(
        greenhouse,

        http_get=greenhouse_get,

        today=today,
    )

    assert checked["decision"] == "review"

    print(
        "PASS: Updated timestamps never substituted "
        "for publication dates"
    )

    # --------------------------------------------------------
    # TEST 5 — DISCOVERY GATE
    # --------------------------------------------------------

    report = check_discovery(
        {
            "verified_open": [],

            "verification_queue": [lead],

            "ats_live_candidates": [],
        },

        max_checks=1,

        http_get=mock({
            "jobs": [fake_job]
        }),

        today=today,
    )

    assert (
        report["ready_for_manual_check"]
        == 1
    )

    assert report["confirmed_open"] == []

    try:

        check_discovery({
            "verified_open": [
                {"title": "Untrusted"}
            ]
        })

    except ValueError:

        pass

    else:

        raise AssertionError(
            "Verified-open inputs must be rejected"
        )

    print(
        "PASS: Discovery gate never fabricates "
        "confirmed-open recommendations"
    )

    print(
        "PASS: All Step 51 checks completed offline; "
        "no CV or LLM requests"
    )


# ============================================================
# 10. COMMAND-LINE ENTRY POINT
# ============================================================

def main() -> None:
    """
    --self-test:
        Run offline verification tests.

    --preview:
        Search real vacancies and check employer ATS
        postings when a supported direct link exists.
    """

    parser = argparse.ArgumentParser(
        description=(
            "JobRadar employer ATS evidence gate"
        )
    )

    actions = (
        parser.add_mutually_exclusive_group(
            required=True
        )
    )

    actions.add_argument(
        "--self-test",
        action="store_true",
    )

    actions.add_argument(
        "--preview",
        action="store_true",
    )

    parser.add_argument(
        "--max-checks",
        type=int,
        default=8,
    )

    args = parser.parse_args()

    if args.self_test:

        run_self_test()

        return

    from src.services.job_discovery import (
        collect_sources,
        prepare_jobs,
    )

    discovery = prepare_jobs(
        collect_sources()
    )

    report = check_discovery(
        discovery,

        max_checks=args.max_checks,
    )

    print(
        "\n======= EMPLOYER ATS VERIFICATION PREVIEW ======="
    )

    print(
        "Direct ATS postings checked:",
        len(report["checked"]),
    )

    print(
        "Review leads without an ATS check:",
        report["without_check"],
    )

    print(
        "Ready for manual application check:",
        report["ready_for_manual_check"],
    )

    print(
        "Review / unavailable evidence:",
        report["review"],
    )

    print(
        "Excluded by official ATS evidence:",
        report["excluded"],
    )

    print(
        "Confirmed-open recommendations: 0"
    )

    for item in report["checked"]:

        print(
            f"- {item['title']}: "
            f"{item['decision']} "
            f"({'; '.join(item['reasons'])})"
        )

    print(
        "No CV uploaded, no applications sent."
    )


if __name__ == "__main__":

    main()