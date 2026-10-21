
"""JobRadar Germany — local dashboard, daily alerts and CV evidence.

The Windows scheduled runner writes public leads to the history database.
This dashboard reads those leads locally; viewing alerts never contacts job APIs.
The live-search button can separately refresh job details and local CV evidence.
All public vacancy leads remain unverified until explicitly confirmed.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import io
import sqlite3
import tempfile
from collections import Counter
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from textwrap import dedent
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
PRIVATE = ROOT / "data" / "private"
TRACKER_DB = PRIVATE / "jobradar_tracker.sqlite3"
HISTORY_DB = PRIVATE / "jobradar_history.sqlite3"
ALERT_STATE_DB = PRIVATE / "jobradar_alert_state.sqlite3"
OWNER = "Ruturaj"
STATUSES = ("Saved", "Applied", "Interview", "Offer", "Rejected", "Withdrawn")
REVIEW_GROUPS = {
    "verification_queue": "Awaiting verification",
    "ats_live_candidates": "ATS-listed (not fully verified)",
}
EXCLUDED_GROUPS = {
    "excluded_stale_or_closed": "Stale / closed / invalid",
    "excluded_foreign": "Outside selected geography",
    "excluded_seniority": "Student / excluded seniority",
}


def safe_url(value: object) -> str | None:
    """Accept normal HTTPS navigation URLs; this does not verify an opening."""
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    if any(char.isspace() for char in value):
        return None
    try:
        p = urlsplit(value)
        if (p.scheme != "https" or not p.hostname or p.username is not None
                or p.password is not None or p.port is not None):
            return None
    except ValueError:
        return None
    return value


@contextmanager
def connection_at(path: Path):
    """Commit/rollback transactions and explicitly close SQLite files on Windows."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), timeout=20)
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def ensure_tracker(db: Path = TRACKER_DB) -> None:
    """Preserve the previous tracker schema and safely migrate follow-up dates."""
    with connection_at(db) as con:
        con.execute("""CREATE TABLE IF NOT EXISTS applications (
            url TEXT PRIMARY KEY, title TEXT NOT NULL, company TEXT NOT NULL,
            location TEXT NOT NULL, status TEXT NOT NULL,
            notes TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL
        )""")
        cols = {r[1] for r in con.execute("PRAGMA table_info(applications)")}
        if "follow_up_date" not in cols:
            con.execute("ALTER TABLE applications ADD COLUMN follow_up_date TEXT NOT NULL DEFAULT ''")


def save_job(job: dict, db: Path = TRACKER_DB) -> bool:
    """Save a lead without overwriting existing notes; never apply automatically."""
    url = safe_url(job.get("url"))
    if url is None:
        raise ValueError("A valid HTTPS job URL is required")
    ensure_tracker(db)
    with connection_at(db) as con:
        cursor = con.execute("""INSERT OR IGNORE INTO applications
            (url,title,company,location,status,notes,updated_at,follow_up_date)
            VALUES (?,?,?,?,'Saved','',?,'')""", (
            url, str(job.get("title") or "Unknown role")[:250],
            str(job.get("company") or "Unknown employer")[:180],
            str(job.get("location") or "Unknown location")[:180],
            datetime.now().astimezone().isoformat(timespec="seconds"),
        ))
        return cursor.rowcount == 1


def tracked_jobs(db: Path = TRACKER_DB) -> list[dict]:
    ensure_tracker(db)
    with connection_at(db) as con:
        con.row_factory = sqlite3.Row
        return [dict(r) for r in con.execute("""SELECT url,title,company,location,status,
            notes,updated_at,follow_up_date FROM applications
            ORDER BY updated_at DESC,title""").fetchall()]


def update_job(url: str, status: str, notes: str, follow_up_date: str = "",
               db: Path = TRACKER_DB) -> None:
    if safe_url(url) is None or status not in STATUSES:
        raise ValueError("Invalid URL or application status")
    if not isinstance(notes, str) or len(notes) > 5000:
        raise ValueError("Notes must be at most 5,000 characters")
    if follow_up_date:
        date.fromisoformat(follow_up_date)
    ensure_tracker(db)
    with connection_at(db) as con:
        cursor = con.execute("""UPDATE applications SET status=?,notes=?,
            follow_up_date=?,updated_at=? WHERE url=?""", (
            status, notes, follow_up_date,
            datetime.now().astimezone().isoformat(timespec="seconds"), url,
        ))
        if cursor.rowcount != 1:
            raise ValueError("Job is not saved in the tracker")


def parse_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except (TypeError, ValueError):
        return None


def parse_timestamp(value: object) -> datetime | None:
    """Normalize saved ISO timestamps for local-time daily alert comparisons."""
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.now().astimezone().tzinfo)
        return dt.astimezone()
    except ValueError:
        return None


def load_history(db: Path = HISTORY_DB) -> tuple[list[dict], list[dict]]:
    """Read existing daily_runner tables. Never fetch jobs or modify history."""
    if not db.is_file():
        return [], []
    with connection_at(db) as con:
        con.row_factory = sqlite3.Row
        leads = [dict(r) for r in con.execute("""SELECT url,title,company,location,
            source,role_priority,geography,review_status,first_seen,last_seen,seen_count
            FROM leads ORDER BY first_seen DESC,title""").fetchall()]
        runs = [dict(r) for r in con.execute("""SELECT run_at,source_records,
            role_matches,review_leads,newly_seen,ats_listed,excluded
            FROM runs ORDER BY run_id DESC LIMIT 30""").fetchall()]
    return leads, runs


def ensure_alert_state(db: Path = ALERT_STATE_DB) -> None:
    """Store only the user's last 'Mark reviewed' time in a separate local DB."""
    with connection_at(db) as con:
        con.execute("""CREATE TABLE IF NOT EXISTS app_state (
            key TEXT PRIMARY KEY,value TEXT NOT NULL)""")


def last_reviewed(db: Path = ALERT_STATE_DB) -> datetime | None:
    ensure_alert_state(db)
    with connection_at(db) as con:
        row = con.execute("SELECT value FROM app_state WHERE key='last_reviewed'").fetchone()
    return parse_timestamp(row[0]) if row else None


def mark_reviewed(db: Path = ALERT_STATE_DB, now: datetime | None = None) -> None:
    ensure_alert_state(db)
    now = now or datetime.now().astimezone()
    with connection_at(db) as con:
        con.execute("""INSERT INTO app_state(key,value) VALUES('last_reviewed',?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value""", (
            now.isoformat(timespec="microseconds"),
        ))


def is_unread(job: dict, reviewed_at: datetime | None) -> bool:
    first = parse_timestamp(job.get("first_seen"))
    return bool(first and (reviewed_at is None or first > reviewed_at))


def filter_alerts(jobs: list[dict], *, period: str = "Today", query: str = "",
                  priorities: list[str] | None = None, sources: list[str] | None = None,
                  geographies: list[str] | None = None, reviewed_at: datetime | None = None,
                  now: datetime | None = None) -> list[dict]:
    """Filter history data in memory; never call external providers."""
    now = now or datetime.now().astimezone()
    needle = query.casefold().strip()
    selectors = (
        ("role_priority", set(priorities or [])),
        ("source", set(sources or [])),
        ("geography", set(geographies or [])),
    )
    filtered = []
    for job in jobs:
        first = parse_timestamp(job.get("first_seen"))
        if not first:
            continue
        days = (now.date() - first.date()).days
        if period == "Today" and days != 0:
            continue
        if period == "Last 7 days" and not 0 <= days < 7:
            continue
        if period == "Unreviewed" and not is_unread(job, reviewed_at):
            continue
        if any(values and str(job.get(field) or "") not in values
               for field, values in selectors):
            continue
        if needle and needle not in " ".join(str(job.get(k) or "") for k in (
            "title", "company", "location", "source",
        )).casefold():
            continue
        filtered.append(job)
    return filtered


def review_leads(results: dict) -> list[dict]:
    """Display only review queues; excluded and confirmed claims stay blocked."""
    seen, output = set(), []
    for key, label in REVIEW_GROUPS.items():
        for job in results.get(key, []) or []:
            if not isinstance(job, dict):
                continue
            identity = job.get("url") or (job.get("source"), job.get("source_id"))
            if identity in seen:
                continue
            seen.add(identity)
            output.append({**job, "_group": label})
    return output


def join_match_evidence(jobs: list[dict], enriched: list[dict]) -> list[dict]:
    """Join by exact URL; withhold ambiguous requirement results."""
    index, duplicate = {}, set()
    for item in enriched:
        if not isinstance(item, dict) or not isinstance(item.get("url"), str):
            continue
        url = item["url"]
        if url in index:
            duplicate.add(url)
        index[url] = item
    return [{**j, "_evidence": (index.get(j.get("url"))
                                if j.get("url") not in duplicate else None)} for j in jobs]


def filter_jobs(records: list[dict], *, query: str = "",
                priorities: list[str] | None = None, geographies: list[str] | None = None,
                sources: list[str] | None = None, groups: list[str] | None = None,
                with_description: bool = False) -> list[dict]:
    selectors = (("role_priority", set(priorities or [])),
                 ("geography", set(geographies or [])),
                 ("source", set(sources or [])),
                 ("_group", set(groups or [])))
    output = []
    for job in records:
        if any(values and str(job.get(key) or "") not in values
               for key, values in selectors):
            continue
        if with_description and not (job.get("_evidence") or {}).get(
                "requirements", {}).get("has_description"):
            continue
        searchable = " ".join(str(job.get(k) or "") for k in (
            "title", "company", "location", "source", "matched_role",
        ))
        if query.strip() and query.casefold().strip() not in searchable.casefold():
            continue
        output.append(job)
    return output


def csv_cell(value: object) -> str:
    text = str(value if value is not None else "")
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) else text


def export_alert_csv(jobs: list[dict]) -> bytes:
    """Export selected public alert rows; no CV information or private notes."""
    output = io.StringIO(newline="")
    fields = ("title", "company", "location", "source", "role_priority", "geography",
              "review_status", "first_seen", "url")
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for job in jobs:
        writer.writerow({key: csv_cell(job.get(key)) for key in fields})
    return output.getvalue().encode("utf-8-sig")


def export_jobs_csv(jobs: list[dict]) -> bytes:
    output = io.StringIO(newline="")
    fields = ("title", "company", "location", "source", "url", "status",
              "requirement_evidence", "score_min", "score_max")
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for job in jobs:
        item = job.get("_evidence") or {}
        bounds = (item.get("matching") or {}).get("possible_score_range") or []
        writer.writerow({
            "title": csv_cell(job.get("title")), "company": csv_cell(job.get("company")),
            "location": csv_cell(job.get("location")), "source": csv_cell(job.get("source")),
            "url": safe_url(job.get("url")) or "", "status": job.get("_group", "Unverified"),
            "requirement_evidence": (item.get("requirements") or {}).get("basis", "unknown"),
            "score_min": bounds[0] if len(bounds) == 2 else "",
            "score_max": bounds[1] if len(bounds) == 2 else "",
        })
    return output.getvalue().encode("utf-8-sig")


def hero_html() -> str:
    """One-line HTML prevents the previously observed Markdown code-block bug."""
    return (
        '<section class="jr-hero">'
        '<div class="jr-kicker">PRIVATE CAREER INTELLIGENCE WORKSPACE</div>'
        '<h1>✦ JobRadar Germany</h1>'
        '<p>Discover job opportunities, review match evidence and follow up in one place.</p>'
        f'<div class="jr-owner">Built for {html.escape(OWNER, quote=True)} · Local-first · Germany</div>'
        '</section>'
    )


def apply_theme(st) -> None:
    st.markdown(dedent("""
        <style>
        [data-testid="stAppViewContainer"] {
            background:radial-gradient(circle at 88% 1%,#202c54 0,transparent 38%),
                linear-gradient(125deg,#080f20,#0f1b31);color:#eef3ff;
        }
        [data-testid="stSidebar"] {
            background:linear-gradient(155deg,#15213d,#0c1528);
            border-right:1px solid rgba(155,180,235,.14);
        }
        .block-container {max-width:1450px;padding-top:1.6rem;padding-bottom:2.5rem;}
        [data-testid="stMetric"] {border:1px solid rgba(155,180,235,.17);
            border-radius:16px;background:linear-gradient(145deg,
            rgba(36,55,92,.85),rgba(16,29,54,.95));padding:1.1rem;}
        .jr-hero {padding:2rem 2.2rem;border-radius:24px;
            background:radial-gradient(circle at 88% 14%,rgba(105,160,255,.25),transparent 38%),
            linear-gradient(115deg,#172e55,#282154);
            border:1px solid rgba(158,187,255,.25);margin:0 0 1.1rem;}
        .jr-kicker {font-size:.73rem;font-weight:800;letter-spacing:.16em;color:#a1e4ff;}
        .jr-hero h1 {color:white;font-size:clamp(2rem,4vw,3rem);line-height:1.18;margin:.5rem 0;}
        .jr-hero p {color:#d2dff5;font-size:1.03rem;max-width:760px;margin:0;}
        .jr-owner {color:#e1c5ff;font-weight:700;font-size:.9rem;margin-top:1.2rem;}
        .jr-brand {font-size:1.35rem;font-weight:900;color:#f5f7ff;}
        .jr-footer {text-align:center;color:#91a2c0;padding:2rem 0;font-size:.85rem;}
        div.stButton>button,div.stDownloadButton>button,div.stLinkButton>a {border-radius:11px;}
        div.stButton>button[kind="primary"] {background:linear-gradient(110deg,#6855ed,#4384ed);
            border:0;color:white;}
        @media(max-width:650px){.jr-hero{padding:1.3rem}.block-container{padding-top:.8rem}}
        </style>
    """).strip(), unsafe_allow_html=True)


def save_button(st, job: dict, key_prefix: str) -> None:
    """Save a public lead to the existing tracker; preserve existing notes."""
    url = safe_url(job.get("url"))
    button_key = key_prefix + hashlib.sha256(str(url or job).encode()).hexdigest()[:16]
    if st.button("＋ Save lead", key=button_key, disabled=url is None,
                 use_container_width=True):
        try:
            added = save_job(job)
            st.success("Saved locally. No application sent." if added else
                       "Already saved — previous notes preserved.")
        except (sqlite3.Error, OSError, ValueError) as exc:
            st.error(f"Could not save: {exc}")


def alert_card(st, job: dict, number: int, reviewed_at: datetime | None) -> None:
    with st.container(border=True):
        c1, c2 = st.columns([5, 2], vertical_alignment="center")
        with c1:
            st.write(f"{number}. {job.get('title') or 'Untitled role'}")
            st.caption(f"{job.get('company') or 'Unknown employer'} · "
                       f"{job.get('location') or 'Unknown location'}")
        with c2:
            if is_unread(job, reviewed_at):
                st.caption("🔔 NEW TO YOUR REVIEW LIST")
            st.caption("UNVERIFIED")
        st.caption(f"{job.get('source') or 'Unknown source'} · "
                   f"{job.get('role_priority') or 'Priority unknown'} · "
                   f"{job.get('geography') or 'Geography unknown'}")
        st.caption(f"First found by JobRadar: {job.get('first_seen') or 'Unknown'} · "
                   f"Seen in {job.get('seen_count') or 1} runs · "
                   f"{job.get('review_status') or 'UNVERIFIED'}")
        a, b, _ = st.columns([1.4, 1.4, 2.8])
        with a:
            if url := safe_url(job.get("url")):
                st.link_button("↗ Job listing", url, use_container_width=True)
        with b:
            save_button(st, job, "alert_save_")


def evidence_panel(st, job: dict) -> None:
    item = job.get("_evidence") or {}
    requirements = item.get("requirements") or {}
    matching = item.get("matching") or {}
    if not requirements or not requirements.get("has_description"):
        st.info("No complete source description linked; requirements remain unknown.")
        return
    st.caption(f"Extraction: {requirements.get('basis') or 'Unknown'} · Provisional")
    skills = requirements.get("required_skills") or []
    domains = requirements.get("required_domains") or []
    languages = requirements.get("required_languages") or {}
    st.write("**Technical skills:**", ", ".join(skills) if skills else "Not confidently extracted")
    st.write("**Experience areas:**", ", ".join(domains) if domains else "Not confidently extracted")
    st.write("**CEFR language levels:**", ", ".join(f"{k} {v}" for k, v in languages.items())
             if languages else "Not established")
    score = matching.get("score")
    bounds = matching.get("possible_score_range") or []
    if score is not None:
        st.metric("Preliminary local match (not a recommendation)", f"{float(score):g}/100")
    elif len(bounds) == 2:
        st.metric("Possible local CV match — not a ranking",
                  f"{float(bounds[0]):g}–{float(bounds[1]):g}/100")
    unknown = matching.get("unknown_dimensions") or []
    if unknown:
        st.caption("Unknown dimensions: " + ", ".join(str(x).replace("_", " ") for x in unknown))
    with st.expander("Original requirements excerpts"):
        evidence = requirements.get("evidence") or {}
        has_any = False
        for group in ("skills", "domains", "languages"):
            for label, excerpts in (evidence.get(group) or {}).items():
                excerpts = [excerpts] if isinstance(excerpts, str) else excerpts
                for excerpt in excerpts or []:
                    has_any = True
                    st.caption(f"{label}: {str(excerpt)[:220]}")
        if not has_any:
            st.caption("No excerpts were extracted.")


def job_card(st, job: dict, number: int) -> None:
    with st.container(border=True):
        a, b = st.columns([5, 2], vertical_alignment="center")
        with a:
            st.write(f"{number}. {job.get('title') or 'Untitled job'}")
            st.caption(f"{job.get('company') or 'Unknown employer'} · "
                       f"{job.get('location') or 'Unknown location'}")
        with b:
            st.caption("UNVERIFIED")
            st.caption(job.get("_group", "Review only"))
        st.caption(" · ".join(str(x) for x in (
            job.get("source"), job.get("role_priority"), job.get("geography")) if x))
        with st.expander("CV match evidence and vacancy checks"):
            evidence_panel(st, job)
            st.divider()
            for label, field in (("Full-time", "full_time_status"),
                                 ("ATS", "ats_status"), ("Seniority", "seniority"),
                                 ("Listing age (days)", "age_days")):
                value = job.get(field)
                st.write(f"**{label}:**", value if value is not None else "Unknown")
            reasons = job.get("review_reasons") or []
            if isinstance(reasons, str):
                reasons = [reasons]
            for reason in reasons:
                st.caption("• " + str(reason))
        a, b, _ = st.columns([1.4, 1.4, 2.8])
        with a:
            if url := safe_url(job.get("url")):
                st.link_button("↗ Original listing", url, use_container_width=True)
        with b:
            save_button(st, job, "search_save_")
        if ats := safe_url(job.get("employer_ats_url")):
            st.link_button("↗ Employer ATS page", ats)


def tracker_view(st, rows: list[dict]) -> None:
    st.subheader("My applications")
    st.caption("Statuses and private notes are saved locally. No automatic applications.")
    if not rows:
        st.info("No saved applications. Save a lead from Daily Alerts or Discover jobs.")
        return
    counts = Counter(row["status"] for row in rows)
    for col, status in zip(st.columns(4), ("Saved", "Applied", "Interview", "Offer")):
        col.metric(status, counts[status])
    today = date.today()
    due = [r for r in rows if (d := parse_date(r["follow_up_date"])) is not None
           and d <= today and r["status"] not in ("Rejected", "Withdrawn", "Offer")]
    if due:
        st.warning(f"{len(due)} saved applications have due or overdue follow-ups.")
    st.dataframe([{"Role": r["title"], "Employer": r["company"], "Status": r["status"],
                   "Follow-up": r["follow_up_date"] or "—"} for r in rows],
                 hide_index=True, use_container_width=True)
    for row in rows:
        uid = hashlib.sha256(row["url"].encode()).hexdigest()[:16]
        with st.expander(f"{row['title']} · {row['company']} — {row['status']}"):
            if url := safe_url(row["url"]):
                st.link_button("↗ Original listing", url)
            existing_date = parse_date(row["follow_up_date"])
            include_date = st.checkbox("Set follow-up date", value=existing_date is not None,
                                       key=f"due_enabled_{uid}")
            with st.form(f"tracking_form_{uid}"):
                stage = row["status"] if row["status"] in STATUSES else "Saved"
                status = st.selectbox("Status", STATUSES, index=STATUSES.index(stage))
                follow = st.date_input("Follow-up date", value=existing_date or today) if include_date else None
                notes = st.text_area("Private notes", value=row["notes"], max_chars=5000)
                submitted = st.form_submit_button("Save changes", type="primary")
            if submitted:
                try:
                    update_job(row["url"], status, notes, follow.isoformat() if follow else "")
                    st.rerun()
                except (sqlite3.Error, OSError, ValueError) as exc:
                    st.error(f"Could not update: {exc}")


def perform_search(max_ba_details: int, ats_checks: int, ba_checks: int,
                   local_matching: bool) -> tuple[dict, int, list[dict], dict]:
    """Reuse existing, tested project modules without modifying their files."""
    from src.services.job_discovery import collect_sources, prepare_jobs
    from src.services.ba_details import build_description_records
    from src.services.job_requirements import enrich_review_leads, extract_requirements
    raw = collect_sources()
    results = prepare_jobs(raw, verify_ats_limit=ats_checks, verify_ba_details=ba_checks)
    if not isinstance(results, dict) or results.get("verified_open"):
        raise ValueError("Unsupported confirmed-open claims: display blocked")
    canonical, stats = build_description_records(raw, results, max_ba_requests=max_ba_details)
    if local_matching:
        from src.services.match_scoring import load_private_profile, load_scoring_config
        enriched = enrich_review_leads(canonical, results, load_private_profile(),
                                       load_scoring_config())
    else:
        descriptions = {r["url"]: r for r in canonical}
        enriched = [{"url": j["url"], "requirements": extract_requirements(
            descriptions.get(j["url"], {})), "matching": None, "confirmed_open": False}
            for group in REVIEW_GROUPS for j in results.get(group, []) or []
            if isinstance(j, dict) and isinstance(j.get("url"), str)]
    return results, len(raw), enriched, stats


def render_alerts(st, history: list[dict], runs: list[dict], reviewed: datetime | None) -> None:
    """Job alerts are entirely local and require no Gmail/Brevo or new API calls."""
    st.subheader("🔔 Daily job alerts")
    st.caption("Updated by your Windows 9 AM task. 'New' means first seen by JobRadar, "
               "not newly published by the employer. All vacancies require verification.")
    if not runs and not history:
        st.info("No local daily reports found. Your 9 AM scheduled run will populate "
                "data/private/jobradar_history.sqlite3. You can also run --run manually.")
        return
    now = datetime.now().astimezone()
    today_count = len(filter_alerts(history, period="Today", now=now))
    unread_count = sum(is_unread(r, reviewed) for r in history)
    latest = runs[0] if runs else {}
    metrics = (("First seen today", today_count), ("Not marked reviewed", unread_count),
               ("New in latest run", latest.get("newly_seen", 0)), ("Total unique leads", len(history)))
    for col, (name, value) in zip(st.columns(4), metrics):
        col.metric(name, value)
    if latest:
        st.caption(f"Last daily run: {latest.get('run_at', 'Unknown')} · "
                   f"Collected {latest.get('source_records', 0)} source records · "
                   f"{latest.get('review_leads', 0)} review leads")
    st.info("These alerts come from local search history and do not include automatic "
            "CV scores. For requirement evidence and score ranges, use Discover jobs "
            "after clicking Run live discovery.")
    st.markdown("#### Your alert inbox")
    a, b = st.columns([2.5, 1], vertical_alignment="center")
    with a:
        period = st.selectbox("Show alerts", ("Today", "Unreviewed", "Last 7 days", "All history"),
                              key="daily_alert_period")
    with b:
        if st.button("✓ Mark all reviewed", key="mark_alerts_reviewed", use_container_width=True):
            try:
                mark_reviewed()
                st.rerun()
            except (OSError, sqlite3.Error) as exc:
                st.error(f"Could not update alert review status: {exc}")
    search = st.text_input("Find a job in your alerts", placeholder="Analyst, Augsburg, employer...",
                           key="alert_search")
    a, b, c = st.columns(3)
    with a:
        priorities = st.multiselect("Priority", sorted({str(x["role_priority"]) for x in history
                                                        if x.get("role_priority")}), key="alert_priority")
    with b:
        sources = st.multiselect("Source", sorted({str(x["source"]) for x in history
                                                    if x.get("source")}), key="alert_source")
    with c:
        areas = st.multiselect("Geography", sorted({str(x["geography"]) for x in history
                                                       if x.get("geography")}), key="alert_geography")
    chosen = filter_alerts(history, period=period, query=search, priorities=priorities,
                           sources=sources, geographies=areas, reviewed_at=reviewed, now=now)
    left, right = st.columns([3, 1], vertical_alignment="center")
    left.caption(f"Showing {len(chosen)} locally archived, unverified job leads")
    with right:
        st.download_button("↓ Export alerts", export_alert_csv(chosen),
                           file_name="jobradar_daily_alerts.csv", mime="text/csv",
                           use_container_width=True)
    page_size = st.selectbox("Alerts per page", (10, 20, 40, 80), index=1)
    pages = max(1, (len(chosen) + page_size - 1) // page_size)
    page = st.number_input("Page", min_value=1, max_value=pages, value=1, step=1,
                           key=f"alert_page_{period}_{len(chosen)}_{page_size}")
    if not chosen:
        st.info("No alerts match this view. Try 'All history' or clear the filters.")
    for i, record in enumerate(chosen[(page - 1) * page_size:page * page_size],
                               start=(page - 1) * page_size + 1):
        alert_card(st, record, i, reviewed)
    with st.expander("Recent scheduled search runs"):
        if runs:
            st.dataframe([{"Run time": r["run_at"], "Source records": r["source_records"],
                           "Review leads": r["review_leads"], "New": r["newly_seen"],
                           "Excluded": r["excluded"]} for r in runs],
                         hide_index=True, use_container_width=True)
        else:
            st.caption("No run history found.")


def run_dashboard() -> None:
    import streamlit as st
    st.set_page_config(page_title=f"JobRadar Germany | {OWNER}", page_icon="✦",
                       layout="wide", initial_sidebar_state="expanded")
    apply_theme(st)
    try:
        history, runs = load_history()
        reviewed = last_reviewed()
        alerts_error = None
    except (sqlite3.Error, OSError) as exc:
        history, runs, reviewed = [], [], None
        alerts_error = str(exc)
    unread = sum(is_unread(x, reviewed) for x in history)

    with st.sidebar:
        st.markdown('<div class="jr-brand">✦ JOBRADAR</div>', unsafe_allow_html=True)
        st.caption(f"Germany · {OWNER}'s workspace")
        st.caption(f"🔔 {unread} job alerts not marked reviewed")
        st.divider()
        st.subheader("Live job discovery")
        st.caption("Optional manual refresh for detailed requirements and local CV matching.")
        max_ba = st.number_input("BA description downloads", min_value=0,
                                 max_value=30, value=12)
        ats_limit = st.number_input("Extra ATS checks", min_value=0, max_value=10, value=0)
        ba_limit = st.number_input("Extra BA vacancy checks", min_value=0, max_value=10, value=0)
        use_profile = st.checkbox("Calculate local CV match ranges", value=True)
        search = st.button("↻ Run live job discovery", type="primary", use_container_width=True)
        if st.button("Clear current live search", use_container_width=True):
            for key in ("radar_results", "radar_count", "radar_time",
                        "radar_enriched", "radar_details_stats"):
                st.session_state.pop(key, None)
            st.rerun()
        st.divider()
        st.caption("Daily alert history is independent of live-search results.")
        st.caption("🔒 Local SQLite · No automatic applications · No Gmail")

    if search:
        with st.spinner("Collecting job leads, BA descriptions and local match evidence..."):
            try:
                results, count, enriched, stats = perform_search(
                    int(max_ba), int(ats_limit), int(ba_limit), use_profile)
                st.session_state["radar_results"] = results
                st.session_state["radar_count"] = count
                st.session_state["radar_time"] = datetime.now().astimezone().strftime("%d %b %Y, %H:%M")
                st.session_state["radar_enriched"] = enriched
                st.session_state["radar_details_stats"] = stats
                st.success("Live job evidence updated; vacancies remain unverified.")
            except Exception as exc:
                st.error(f"Live search failed ({type(exc).__name__}): {exc}")

    st.markdown(hero_html(), unsafe_allow_html=True)
    st.warning("**Verification gate active:** JobRadar alerts are public leads, not confirmed "
               "open vacancies. Check the employer's current application page before applying.")
    if alerts_error:
        st.error(f"Could not read local daily history: {alerts_error}")
    results = st.session_state.get("radar_results") or {}
    leads = join_match_evidence(review_leads(results), st.session_state.get("radar_enriched") or [])
    stats = st.session_state.get("radar_details_stats") or {}
    try:
        saved = tracked_jobs()
    except (sqlite3.Error, OSError) as exc:
        st.error(f"Local application tracker unavailable: {exc}")
        saved = []

    overview, alerts_tab, discover, evidence_tab, audit, tracker, about = st.tabs((
        "▦ Overview", "🔔 Daily Alerts", "⌕ Discover jobs", "◈ Match evidence",
        "◷ Excluded / audit", "▤ My applications", "ⓘ About",
    ))

    with overview:
        st.subheader(f"Welcome back, {OWNER}")
        today_count = len(filter_alerts(history, period="Today"))
        metrics = (("New leads today", today_count), ("Unreviewed alerts", unread),
                   ("Live review leads", len(leads)), ("Saved applications", len(saved)))
        for col, (label, val) in zip(st.columns(4), metrics):
            col.metric(label, val)
        if runs:
            st.caption("Latest scheduled search: " + str(runs[0].get("run_at")))
        else:
            st.info("Daily alerts will appear here once the scheduled runner completes a search.")
        if results:
            st.markdown("#### Last manual live-search quality gates")
            chart = [{"Stage": label, "Jobs": len(results.get(key, []) or [])}
                     for key, label in list(REVIEW_GROUPS.items()) + list(EXCLUDED_GROUPS.items())]
            st.bar_chart(chart, x="Stage", y="Jobs")
            st.caption("Last manual search: " + st.session_state.get("radar_time", "Unknown"))
        if saved:
            st.markdown("#### Recently saved applications")
            st.dataframe([{"Role": j["title"], "Employer": j["company"],
                           "Status": j["status"]} for j in saved[:5]],
                         hide_index=True, use_container_width=True)

    with alerts_tab:
        render_alerts(st, history, runs, reviewed)

    with discover:
        st.subheader("Explore current job leads")
        st.caption("A manual live search loads detailed requirements and local CV score ranges.")
        if not results:
            st.info("Click Run live job discovery in the sidebar for real-time evidence.")
        else:
            query = st.text_input("Search job titles, employers or locations", key="job_search")
            a, b, c = st.columns(3)
            with a:
                priorities = st.multiselect("Role priority", sorted({str(j["role_priority"])
                    for j in leads if j.get("role_priority")}), key="job_priority")
            with b:
                geography = st.multiselect("Geography", sorted({str(j["geography"])
                    for j in leads if j.get("geography")}), key="job_area")
            with c:
                sources = st.multiselect("Source", sorted({str(j["source"])
                    for j in leads if j.get("source")}), key="job_source")
            groups = st.multiselect("Verification group", list(REVIEW_GROUPS.values()))
            with_desc = st.checkbox("Only with description")
            selected = filter_jobs(leads, query=query, priorities=priorities,
                                   geographies=geography, sources=sources, groups=groups,
                                   with_description=with_desc)
            a, b = st.columns([3, 1], vertical_alignment="center")
            a.caption(f"{len(selected)} unverified review leads")
            with b:
                st.download_button("↓ Export CSV", export_jobs_csv(selected),
                                   file_name="jobradar_review_leads.csv", mime="text/csv",
                                   use_container_width=True)
            per_page = st.selectbox("Job cards per page", (10, 20, 40, 80), index=1)
            pages = max(1, (len(selected) + per_page - 1) // per_page)
            page = st.number_input("Job page", min_value=1, max_value=pages, value=1, step=1,
                                   key=f"job_page_{len(selected)}_{per_page}")
            if not selected:
                st.info("No jobs match those filters.")
            for i, job in enumerate(selected[(page - 1) * per_page:page * per_page],
                                    start=(page - 1) * per_page + 1):
                job_card(st, job, i)

    with evidence_tab:
        st.subheader("Qualification and CV match evidence")
        if not results:
            st.info("Run live job discovery to extract descriptions and see local fit ranges.")
        else:
            count_description = sum(bool((j.get("_evidence") or {}).get("requirements", {})
                                         .get("has_description")) for j in leads)
            count_skills = sum(bool((j.get("_evidence") or {}).get("requirements", {})
                                    .get("required_skills")) for j in leads)
            count_ranges = sum(bool((j.get("_evidence") or {}).get("matching", {})
                                    .get("possible_score_range")) for j in leads)
            for c, (label, value) in zip(st.columns(3), (("Descriptions", count_description),
                    ("Leads with skills", count_skills), ("Leads with match ranges", count_ranges))):
                c.metric(label, value)
            st.caption(f"BA detail requests: {stats.get('ba_requests', 0)} · "
                       f"Descriptions retrieved: {stats.get('ba_descriptions', 0)}")
            skills = Counter(skill for job in leads for skill in ((job.get("_evidence") or {})
                .get("requirements", {}).get("required_skills") or []))
            if skills:
                st.bar_chart([{"Skill": skill, "Jobs": n} for skill, n in skills.most_common(12)],
                             x="Skill", y="Jobs")
            lines = []
            for job in leads:
                details = job.get("_evidence") or {}
                req = details.get("requirements") or {}
                match = details.get("matching") or {}
                bounds = match.get("possible_score_range") or []
                lines.append({"Role": job.get("title"), "Employer": job.get("company"),
                              "Description": "Yes" if req.get("has_description") else "No",
                              "Skills": len(req.get("required_skills") or []),
                              "Possible match": f"{bounds[0]:g}–{bounds[1]:g}"
                              if len(bounds) == 2 else "Not assessed"})
            st.dataframe(lines, hide_index=True, use_container_width=True)
            st.info("Provisional scores are not hiring probabilities or verified recommendations.")

    with audit:
        st.subheader("Excluded vacancies · audit")
        if not results:
            st.info("Run a manual search to view exclusion categories.")
        else:
            choice = st.selectbox("Exclusion category", list(EXCLUDED_GROUPS.values()))
            key = next(k for k, label in EXCLUDED_GROUPS.items() if label == choice)
            excluded = results.get(key, []) or []
            st.metric("Excluded records", len(excluded))
            st.dataframe([{"Job": j.get("title"), "Employer": j.get("company"),
                           "Location": j.get("location"), "Reasons": "; ".join(
                           map(str, [j.get("review_reasons")] if isinstance(
                           j.get("review_reasons"), str) else j.get("review_reasons") or []))}
                          for j in excluded if isinstance(j, dict)],
                         hide_index=True, use_container_width=True)

    with tracker:
        tracker_view(st, saved)

    with about:
        st.subheader("About JobRadar Germany")
        st.write("**Daily alerts:** read from data/private/jobradar_history.sqlite3, updated "
                 "by the existing Windows scheduled task at 9 AM.")
        st.write("**Unreviewed alerts:** stay highlighted until you choose Mark all reviewed. "
                 "That action affects only the alert marker, not job history or applications.")
        st.write("**Live scoring:** uses public job descriptions and the professional profile "
                 "on this computer. Unknown dimensions remain unknown.")
        st.write("**Privacy:** no Gmail or Brevo configuration, no CV uploads, "
                 "no automatic job applications.")
        st.write("**Limitation:** history alerts do not include CV scores; direct employer "
                 "application acceptance remains unverified.")

    st.markdown('<div class="jr-footer">✦ JobRadar Germany · Made for '
                + html.escape(OWNER) + ' · Private local workspace</div>',
                unsafe_allow_html=True)


def run_self_test() -> None:
    """Offline regressions, including daily_runner schema and Windows file cleanup."""
    assert hero_html().startswith('<section class="jr-hero">') and "\n" not in hero_html()
    assert safe_url("https://example.org/job/1")
    assert safe_url("javascript:alert(1)") is None
    print("PASS: HTML rendering and HTTPS links")
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        history_db = root / "history.sqlite3"
        state_db = root / "state.sqlite3"
        tracker_db = root / "tracker.sqlite3"
        now = datetime.now().astimezone()
        yesterday = now - timedelta(days=1)
        with connection_at(history_db) as con:
            con.execute("""CREATE TABLE leads (url TEXT PRIMARY KEY, title TEXT NOT NULL,
                company TEXT NOT NULL, location TEXT NOT NULL, source TEXT NOT NULL,
                role_priority TEXT NOT NULL, geography TEXT NOT NULL,
                review_status TEXT NOT NULL, first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL, seen_count INTEGER NOT NULL DEFAULT 1)""")
            con.execute("""CREATE TABLE runs (run_id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT NOT NULL, source_records INTEGER NOT NULL,
                role_matches INTEGER NOT NULL, review_leads INTEGER NOT NULL,
                newly_seen INTEGER NOT NULL, ats_listed INTEGER NOT NULL,
                excluded INTEGER NOT NULL)""")
            for num, dt in ((1, now), (2, yesterday)):
                con.execute("""INSERT INTO leads VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (
                    f"https://example.org/{num}", "=Formula" if num == 1 else "BI Analyst",
                    "Example employer", "Augsburg", "BA", "A", "preferred_city",
                    "UNVERIFIED", dt.isoformat(timespec="microseconds"),
                    now.isoformat(timespec="microseconds"), num,
                ))
            con.execute("""INSERT INTO runs(run_at,source_records,role_matches,
                review_leads,newly_seen,ats_listed,excluded) VALUES(?,?,?,?,?,?,?)""",
                (now.isoformat(), 979, 75, 20, 1, 0, 25))
        jobs, runs = load_history(history_db)
        assert len(jobs) == 2 and len(runs) == 1 and runs[0]["source_records"] == 979
        assert len(filter_alerts(jobs, now=now)) == 1
        assert len(filter_alerts(jobs, period="Last 7 days", now=now)) == 2
        assert len(filter_alerts(jobs, period="Unreviewed", now=now)) == 2
        mark_reviewed(state_db, now=now + timedelta(seconds=1))
        mark = last_reviewed(state_db)
        assert mark is not None
        assert not any(is_unread(j, mark) for j in jobs)
        assert not filter_alerts(jobs, period="Unreviewed", reviewed_at=mark, now=now)
        assert b"'=Formula" in export_alert_csv(jobs)
        print("PASS: Daily history, date filters, review marker and CSV safety")
        lead = {"url": "https://example.org/1", "title": "Test", "company": "Example",
                "location": "Augsburg"}
        assert save_job(lead, tracker_db) is True
        assert save_job(lead, tracker_db) is False
        update_job(lead["url"], "Applied", "Sent manually", "2026-11-02", tracker_db)
        assert tracked_jobs(tracker_db)[0]["notes"] == "Sent manually"
        match = join_match_evidence([lead], [{"url": lead["url"], "requirements": {
            "has_description": True}, "matching": {"possible_score_range": [25.0, 75.0]}}])
        assert b"25.0" in export_jobs_csv(match)
        print("PASS: Tracker, duplicates and provisional match export")
        for path in (history_db, state_db, tracker_db):
            path.unlink()
            assert not path.exists()
        print("PASS: All SQLite connections closed (Windows-safe)")
    print("PASS: Dashboard tests complete — no real API, CV or email calls")


def main() -> None:
    parser = argparse.ArgumentParser(description="JobRadar Germany Streamlit dashboard")
    parser.add_argument("--self-test", action="store_true")
    args, _ = parser.parse_known_args()
    run_self_test() if args.self_test else run_dashboard()


if __name__ == "__main__":
    main()
