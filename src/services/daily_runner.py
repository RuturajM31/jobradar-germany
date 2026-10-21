"""
JobRadar Germany — Local Daily Search, History and Email Digest

THEORY
------
A recurring ETL workflow should be idempotent: seeing the same job twice
must not make it appear as a newly discovered vacancy twice.

This module calls the existing public job discovery pipeline, archives
review leads in local SQLite, creates a concise local report, and can
optionally email a digest containing PUBLIC job metadata only.

GUARDRAILS
----------
- No CV/profile file is read or transmitted.
- No LLM calls or automatic job applications.
- No vacancy is promoted to confirmed-open.
- Emails are sent only with an explicit --send-email flag.
- A job's last-seen date is NOT treated as its publication date.
"""

from __future__ import annotations

import argparse
import csv
import io
import os
import smtplib
import sqlite3
import ssl
import tempfile

from contextlib import contextmanager
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[2]

HISTORY_DB = (
    ROOT / "data" / "private" / "jobradar_history.sqlite3"
)

REPORT_DIR = (
    ROOT / "data" / "private" / "reports"
)

REVIEW_GROUPS = (
    ("verification_queue", "UNVERIFIED"),
    (
        "ats_live_candidates",
        "ATS-LISTED — NOT FULLY VERIFIED",
    ),
)

EXCLUSION_GROUPS = (
    "excluded_stale_or_closed",
    "excluded_foreign",
    "excluded_seniority",
)


# ============================================================
# 1. VALIDATE PUBLIC JOB URLS
# ============================================================

def safe_https(value: object) -> str | None:
    """
    Accept normal HTTPS URLs, never script/file links.
    """

    if not isinstance(value, str) or not value.strip():
        return None

    value = value.strip()

    if any(ch.isspace() for ch in value):
        return None

    try:
        p = urlsplit(value)

        if (
            p.scheme != "https"
            or not p.hostname
            or p.username is not None
            or p.password is not None
            or p.port is not None
        ):
            return None

    except ValueError:
        return None

    return value


# ============================================================
# 2. PREPARE PUBLIC REVIEW LEADS
# ============================================================

def public_review_leads(discovery: dict) -> list[dict]:
    """
    Only keep minimal, review-eligible public job fields.

    Do not include:
    - CV information
    - Personal contact information
    - Private application notes
    - Invented verification claims
    """

    if (
        not isinstance(discovery, dict)
        or discovery.get("verified_open")
    ):
        raise ValueError(
            "Unexpected confirmed-open output: archive refused"
        )

    items: list[dict] = []
    seen: set[str] = set()

    for group, label in REVIEW_GROUPS:

        for job in discovery.get(group, []) or []:

            if not isinstance(job, dict):
                continue

            url = safe_https(
                job.get("url")
            )

            if not url or url in seen:
                continue

            seen.add(url)

            items.append({
                "url": url,

                "title": str(
                    job.get("title") or "Unknown role"
                )[:250],

                "company": str(
                    job.get("company") or "Unknown employer"
                )[:180],

                "location": str(
                    job.get("location") or "Unknown location"
                )[:180],

                "source": str(
                    job.get("source") or "Unknown source"
                )[:100],

                "role_priority": str(
                    job.get("role_priority") or "unknown"
                )[:50],

                "geography": str(
                    job.get("geography") or "unknown"
                )[:80],

                "review_status": label,
            })

    return items


# ============================================================
# 3. WINDOWS-SAFE SQLITE CONNECTIONS
# ============================================================

@contextmanager
def connection_at(db: Path):
    """
    Commit or roll back, and ALWAYS close SQLite handles.

    Important on Windows:
    A transaction context manager alone does not
    necessarily release the database file handle.
    """

    db.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    con = sqlite3.connect(
        str(db),
        timeout=20,
    )

    try:
        yield con
        con.commit()

    except Exception:
        con.rollback()
        raise

    finally:
        con.close()


# ============================================================
# 4. CREATE SEARCH HISTORY TABLES
# ============================================================

def ensure_history(con: sqlite3.Connection) -> None:
    """
    Create separate history tables.

    This database never modifies the existing
    application tracker.
    """

    con.execute(
        """
        CREATE TABLE IF NOT EXISTS leads (
            url TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            company TEXT NOT NULL,
            location TEXT NOT NULL,
            source TEXT NOT NULL,
            role_priority TEXT NOT NULL,
            geography TEXT NOT NULL,
            review_status TEXT NOT NULL,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            seen_count INTEGER NOT NULL DEFAULT 1
        )
        """
    )

    con.execute(
        """
        CREATE TABLE IF NOT EXISTS runs (
            run_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_at TEXT NOT NULL,
            source_records INTEGER NOT NULL,
            role_matches INTEGER NOT NULL,
            review_leads INTEGER NOT NULL,
            newly_seen INTEGER NOT NULL,
            ats_listed INTEGER NOT NULL,
            excluded INTEGER NOT NULL
        )
        """
    )


# ============================================================
# 5. SAVE A DAILY SEARCH
# ============================================================

def archive_run(
    discovery: dict,
    source_count: int,
    *,
    db: Path = HISTORY_DB,
    now: datetime | None = None,
) -> tuple[dict, list[dict]]:
    """
    Persist one run.

    Returns:
        Search summary
        Newly discovered review leads

    THEORY:
    A previously stored URL is not a new job,
    even if its title changes.

    first_seen means when JobRadar first observed
    the listing, not its original publication date.
    """

    leads = public_review_leads(
        discovery
    )

    now = now or datetime.now().astimezone()

    timestamp = now.isoformat(
        timespec="seconds"
    )

    excluded = sum(
        len(discovery.get(key, []) or [])
        for key in EXCLUSION_GROUPS
    )

    ats_listed = sum(
        1
        for job in leads
        if job["review_status"].startswith(
            "ATS-LISTED"
        )
    )

    new_leads: list[dict] = []

    with connection_at(db) as con:

        ensure_history(con)

        for job in leads:

            exists = con.execute(
                "SELECT 1 FROM leads WHERE url = ?",
                (job["url"],),
            ).fetchone()

            if exists:

                con.execute(
                    """
                    UPDATE leads

                    SET
                        title = ?,
                        company = ?,
                        location = ?,
                        source = ?,
                        role_priority = ?,
                        geography = ?,
                        review_status = ?,
                        last_seen = ?,
                        seen_count = seen_count + 1

                    WHERE url = ?
                    """,
                    (
                        job["title"],
                        job["company"],
                        job["location"],
                        job["source"],
                        job["role_priority"],
                        job["geography"],
                        job["review_status"],
                        timestamp,
                        job["url"],
                    ),
                )

            else:

                con.execute(
                    """
                    INSERT INTO leads (
                        url,
                        title,
                        company,
                        location,
                        source,
                        role_priority,
                        geography,
                        review_status,
                        first_seen,
                        last_seen,
                        seen_count
                    )

                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                    """,
                    (
                        job["url"],
                        job["title"],
                        job["company"],
                        job["location"],
                        job["source"],
                        job["role_priority"],
                        job["geography"],
                        job["review_status"],
                        timestamp,
                        timestamp,
                    ),
                )

                new_leads.append(job)

        con.execute(
            """
            INSERT INTO runs (
                run_at,
                source_records,
                role_matches,
                review_leads,
                newly_seen,
                ats_listed,
                excluded
            )

            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                timestamp,

                source_count,

                int(
                    discovery.get("title_matches") or 0
                ),

                len(leads),

                len(new_leads),

                ats_listed,

                excluded,
            ),
        )

    summary = {
        "timestamp": timestamp,

        "source_records": source_count,

        "role_matches": int(
            discovery.get("title_matches") or 0
        ),

        "review_leads": len(leads),

        "new_leads": len(new_leads),

        "ats_listed": ats_listed,

        "excluded": excluded,

        "confirmed_open": 0,
    }

    return summary, new_leads


# ============================================================
# 6. SAFE CSV EXPORT
# ============================================================

def safe_csv_cell(value: object) -> str:
    """
    Prevent spreadsheet interpretation of untrusted titles.
    """

    result = str(
        value if value is not None else ""
    )

    if result.lstrip().startswith(
        ("=", "+", "-", "@")
    ):
        return "'" + result

    return result


def csv_bytes(leads: list[dict]) -> bytes:
    """
    Create a CSV of newly discovered PUBLIC leads.
    """

    out = io.StringIO(
        newline=""
    )

    fields = (
        "title",
        "company",
        "location",
        "source",
        "role_priority",
        "geography",
        "review_status",
        "url",
    )

    writer = csv.DictWriter(
        out,
        fieldnames=fields,
    )

    writer.writeheader()

    for job in leads:

        writer.writerow({
            key: safe_csv_cell(
                job.get(key)
            )
            for key in fields
        })

    return out.getvalue().encode(
        "utf-8-sig"
    )


# ============================================================
# 7. GENERATE TEXT DIGEST
# ============================================================

def digest_text(
    summary: dict,
    new_leads: list[dict],
    max_links: int = 15,
) -> str:
    """
    Prepare a text report without any profile data.

    Only public vacancy metadata is included.
    """

    lines = [
        "JOBRADAR GERMANY — DAILY PUBLIC JOB LEADS",

        "ALL LEADS UNVERIFIED — NOT JOB RECOMMENDATIONS",

        "",

        f"Run: {summary['timestamp']}",

        f"Public source records: {summary['source_records']}",

        f"Relevant role matches: {summary['role_matches']}",

        f"Review leads today: {summary['review_leads']}",

        f"New leads not in local history: {summary['new_leads']}",

        f"ATS-listed but not fully verified: {summary['ats_listed']}",

        f"Excluded by existing quality gates: {summary['excluded']}",

        "Confirmed-open recommendations: 0",

        "",

        "NEW REVIEW LEADS",
    ]

    if not new_leads:

        lines.append(
            "No newly discovered review leads in this run."
        )

    for index, job in enumerate(
        new_leads[:max_links],
        1,
    ):

        lines.extend([
            (
                f"{index}. {job['title']} — "
                f"{job['company']} ({job['location']})"
            ),

            f"   Status: {job['review_status']}",

            f"   Source: {job['url']}",
        ])

    if len(new_leads) > max_links:

        lines.append(
            f"... plus {len(new_leads) - max_links} "
            "more in the local CSV."
        )

    lines.extend([
        "",

        "Opening a source link is not confirmation "
        "of vacancy availability.",

        "This report contains only public job metadata, "
        "no CV or private notes.",
    ])

    return "\n".join(lines) + "\n"


# ============================================================
# 8. SAVE DAILY REPORT FILES
# ============================================================

def write_reports(
    summary: dict,
    new_leads: list[dict],
    *,
    directory: Path = REPORT_DIR,
    now: datetime | None = None,
) -> tuple[Path, Path]:
    """
    Write local text and CSV reports.

    Both are saved inside data/private/reports/.
    """

    directory.mkdir(
        parents=True,
        exist_ok=True,
    )

    now = now or datetime.now().astimezone()

    label = now.strftime(
        "%Y%m%d_%H%M%S_%f"
    )

    text_path = (
        directory / f"jobradar_{label}.txt"
    )

    csv_path = (
        directory / f"jobradar_{label}.csv"
    )

    text_path.write_text(
        digest_text(
            summary,
            new_leads,
        ),
        encoding="utf-8",
    )

    csv_path.write_bytes(
        csv_bytes(new_leads)
    )

    return text_path, csv_path


# ============================================================
# 9. OPTIONAL SMTP EMAIL DIGEST
# ============================================================

def send_digest(body: str) -> None:
    """
    Send the public-only digest using SMTP STARTTLS.

    IMPORTANT:
    This function is called only when the user explicitly
    specifies --send-email.

    Required environment variables:

        JOBRADAR_SMTP_HOST
        JOBRADAR_SMTP_USER
        JOBRADAR_SMTP_PASSWORD
        JOBRADAR_EMAIL_TO

    Optional:

        JOBRADAR_SMTP_PORT
        JOBRADAR_EMAIL_FROM

    Passwords are never stored in the Python source code.
    """

    host = os.environ.get(
        "JOBRADAR_SMTP_HOST",
        "",
    ).strip()

    username = os.environ.get(
        "JOBRADAR_SMTP_USER",
        "",
    ).strip()

    password = os.environ.get(
        "JOBRADAR_SMTP_PASSWORD",
        "",
    )

    recipient = os.environ.get(
        "JOBRADAR_EMAIL_TO",
        "",
    ).strip()

    sender = (
        os.environ.get(
            "JOBRADAR_EMAIL_FROM",
            "",
        ).strip()
        or username
    )

    port_str = os.environ.get(
        "JOBRADAR_SMTP_PORT",
        "587",
    )

    if not all((
        host,
        username,
        password,
        recipient,
        sender,
    )):

        raise ValueError(
            "SMTP details missing in "
            "JOBRADAR_* environment variables"
        )

    if any(
        "\n" in item or "\r" in item
        for item in (
            recipient,
            sender,
        )
    ):

        raise ValueError(
            "Invalid email header characters"
        )

    port = int(
        port_str
    )

    if not 1 <= port <= 65535:

        raise ValueError(
            "Invalid SMTP port"
        )

    message = EmailMessage()

    message["Subject"] = (
        "JobRadar Germany — Daily Unverified Job Leads"
    )

    message["From"] = sender

    message["To"] = recipient

    message.set_content(
        body
    )

    with smtplib.SMTP(
        host,
        port,
        timeout=20,
    ) as server:

        server.ehlo()

        server.starttls(
            context=ssl.create_default_context()
        )

        server.ehlo()

        server.login(
            username,
            password,
        )

        server.send_message(
            message
        )


# ============================================================
# 10. VIEW LOCAL SEARCH HISTORY
# ============================================================

def show_history(
    db: Path = HISTORY_DB,
    limit: int = 10,
) -> None:
    """
    Show recent archived search counts.

    Application notes and CV information are not read.
    """

    if not db.exists():

        print(
            "No previous daily runs archived yet."
        )

        return

    with connection_at(db) as con:

        ensure_history(con)

        rows = con.execute(
            """
            SELECT
                run_at,
                source_records,
                review_leads,
                newly_seen

            FROM runs

            ORDER BY run_id DESC

            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    for run_at, sources, review, new in rows:

        print(
            f"{run_at} | "
            f"sources={sources} | "
            f"review={review} | "
            f"new={new}"
        )

    if not rows:

        print(
            "No previous daily runs archived yet."
        )


# ============================================================
# 11. RUN REAL DAILY JOB DISCOVERY
# ============================================================

def run_live(
    send_email: bool = False,
) -> None:
    """
    Run one complete public job-search cycle.

    Uses the existing, tested JobRadar pipeline.

    This is the entry point Windows Task Scheduler
    can execute once per day.
    """

    from src.services.job_discovery import (
        collect_sources,
        prepare_jobs,
    )

    raw_jobs = collect_sources()

    discovery = prepare_jobs(
        raw_jobs
    )

    summary, new_leads = archive_run(
        discovery,
        len(raw_jobs),
    )

    text_path, csv_path = write_reports(
        summary,
        new_leads,
    )

    print(
        "\n======= JOBRADAR DAILY RUN ======="
    )

    print(
        "Review leads:",
        summary["review_leads"],
    )

    print(
        "New leads since previous runs:",
        summary["new_leads"],
    )

    print(
        "Confirmed open:",
        summary["confirmed_open"],
    )

    print(
        "Text report:",
        text_path,
    )

    print(
        "CSV report:",
        csv_path,
    )

    print(
        "No private profile loaded; "
        "no application submitted."
    )

    if send_email:

        send_digest(
            digest_text(
                summary,
                new_leads,
            )
        )

        print(
            "Public-only summary email sent "
            "(explicit opt-in)."
        )

    else:

        print(
            "Email not sent; --send-email "
            "was not requested."
        )


# ============================================================
# 12. DETERMINISTIC OFFLINE TESTS
# ============================================================

def run_self_test() -> None:
    """
    Test:
        Deduplication
        Search history
        CSV safety
        Local report generation
        Windows SQLite cleanup

    Tests use fictional jobs only.
    """

    test_now = datetime(
        2026,
        10,
        21,
        9,
        0,
        tzinfo=timezone.utc,
    )

    fake = {
        "title_matches": 2,

        "verified_open": [],

        "verification_queue": [
            {
                "url": "https://example.org/job/1",

                "title": "Data Analyst",

                "company": "A",

                "location": "Augsburg",

                "source": "BA",
            },

            {
                "url": "https://example.org/job/2",

                "title": "=HYPERLINK(1)",

                "company": "B",

                "location": "Munich",

                "source": "Arbeitnow",
            },
        ],

        "ats_live_candidates": [
            {
                "url": "https://example.org/job/1",

                "title": "Duplicate",

                "company": "A",
            },
        ],

        "excluded_stale_or_closed": [
            {
                "url": "https://example.org/expired",
            },
        ],

        "excluded_foreign": [],

        "excluded_seniority": [],
    }

    # --------------------------------------------------------
    # TEST 1 — REVIEW LEAD DEDUPLICATION
    # --------------------------------------------------------

    assert len(
        public_review_leads(fake)
    ) == 2

    print(
        "PASS: Excluded records and duplicate URLs "
        "stay out of review history"
    )

    # --------------------------------------------------------
    # TEST 2 — SQLITE HISTORY
    # --------------------------------------------------------

    with tempfile.TemporaryDirectory() as folder:

        db = (
            Path(folder)
            / "private"
            / "history.sqlite3"
        )

        report_dir = (
            Path(folder)
            / "reports"
        )

        summary, new = archive_run(
            fake,
            50,
            db=db,
            now=test_now,
        )

        assert summary["new_leads"] == 2

        assert summary["review_leads"] == 2

        # Run the same search again.
        # Previously seen URLs must not be new.
        second, repeated = archive_run(
            fake,
            50,
            db=db,
            now=test_now,
        )

        assert second["new_leads"] == 0

        assert repeated == []

        with connection_at(db) as con:

            rows = con.execute(
                """
                SELECT seen_count
                FROM leads
                ORDER BY url
                """
            ).fetchall()

            runs = con.execute(
                """
                SELECT count(*)
                FROM runs
                """
            ).fetchone()[0]

        assert rows == [
            (2,),
            (2,),
        ]

        assert runs == 2

        print(
            "PASS: SQLite history deduplicates "
            "across repeated runs"
        )

        # ----------------------------------------------------
        # TEST 3 — REPORT GENERATION
        # ----------------------------------------------------

        text_path, csv_path = write_reports(
            summary,
            new,

            directory=report_dir,

            now=test_now,
        )

        assert text_path.exists()

        assert csv_path.exists()

        assert (
            b"'=HYPERLINK"
            in csv_path.read_bytes()
        )

        assert (
            "UNVERIFIED"
            in text_path.read_text(
                encoding="utf-8"
            )
        )

        assert (
            "CV"
            in digest_text(
                summary,
                new,
            )
        )

        print(
            "PASS: Local text/CSV reports preserve "
            "unverified labels and CSV safety"
        )

        # ----------------------------------------------------
        # TEST 4 — WINDOWS SQLITE LOCKING
        # ----------------------------------------------------

        db.unlink()

        assert not db.exists()

    print(
        "PASS: Windows SQLite connections released; "
        "no network, CV or email calls"
    )


# ============================================================
# 13. COMMAND-LINE INTERFACE
# ============================================================

def main() -> None:
    """
    Available commands:

        --self-test
            Run offline tests.

        --run
            Run job discovery and save a report.

        --history
            Display previous search summaries.

        --run --send-email
            Explicitly send a public-only email digest.
    """

    parser = argparse.ArgumentParser(
        description=(
            "JobRadar local daily run and reporting"
        )
    )

    mode = parser.add_mutually_exclusive_group(
        required=True
    )

    mode.add_argument(
        "--self-test",
        action="store_true",
        help="Run offline tests",
    )

    mode.add_argument(
        "--run",
        action="store_true",
        help=(
            "Collect, archive and report public job leads"
        ),
    )

    mode.add_argument(
        "--history",
        action="store_true",
        help="Show previous daily search runs",
    )

    parser.add_argument(
        "--send-email",
        action="store_true",
        help=(
            "Explicitly opt in to emailing "
            "PUBLIC job metadata"
        ),
    )

    args = parser.parse_args()

    if args.self_test:

        run_self_test()

    elif args.history:

        show_history()

    else:

        run_live(
            send_email=args.send_email
        )


if __name__ == "__main__":
    main()