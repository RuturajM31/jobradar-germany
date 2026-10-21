"""
Day 11 — JobRadar Germany
Module: Seniority Filter

THEORY:
Simple, predictable tasks should use Python rules
instead of consuming AI model calls.

WHAT THIS MODULE DOES:
1. Identifies seniority from job titles.
2. Excludes internships and student positions.
3. Prioritizes Mid-level and Senior positions.
4. Keeps Junior, Lead, and unspecified roles for review.

IMPORTANT:
Job titles do not always reveal seniority accurately.
CrewAI will later examine full job descriptions.
"""

import re

from src.sources.arbeitnow import fetch_jobs
from src.services.job_filter import load_roles, filter_jobs
from src.services.location_filter import (
    load_location_settings,
    classify_location,
)


def classify_seniority(title: str) -> tuple[str, str]:
    """Return the seniority level and its priority."""

    title = title.casefold()

    # Student and internship positions are not suitable
    # for our main job search.
    if re.search(
        r"werkstudent\w*|working[\s-]*student|"
        r"praktik\w*|\bintern(ship)?\b|\bausbildung\b",
        title,
    ):
        return "Student / Internship", "excluded"

    # Senior positions are our first priority.
    if re.search(r"\bsenior\b|\bsr\.?\b", title):
        return "Senior", "preferred"

    # Lead opportunities are considered individually.
    if re.search(r"\blead\b|\bprincipal\b", title):
        return "Lead", "consider"

    # Junior opportunities may still be worthwhile.
    if re.search(r"\bjunior\b|\bjr\.?\b", title):
        return "Junior", "consider"

    # Some advertisements specify Mid-level explicitly.
    if re.search(r"\bmid[\s-]*level\b", title):
        return "Mid-level", "preferred"

    # Never assume a role is junior or senior
    # simply because the title doesn't say.
    return "Not specified", "review"


def main():
    """Find suitable roles and check their seniority."""

    print("JobRadar Germany — Seniority Filtering")
    print("--------------------------------------")

    # Fetch jobs and apply our existing role filter.
    jobs = fetch_jobs()
    role_matches = filter_jobs(jobs, load_roles())

    # Apply our previously configured location preferences.
    location_settings = load_location_settings()

    preferred_locations = [
        job for job in role_matches
        if classify_location(job, location_settings)
        != "needs_review"
    ]

    suitable = []
    excluded = []

    for job in preferred_locations:
        level, priority = classify_seniority(job["title"])

        job["seniority"] = level
        job["seniority_priority"] = priority

        if priority == "excluded":
            excluded.append(job)
        else:
            suitable.append(job)

    # Show preferred positions first, followed by
    # additional positions requiring review.
    priority_order = {
        "preferred": 0,
        "consider": 1,
        "review": 2,
    }

    suitable.sort(
        key=lambda job: priority_order[job["seniority_priority"]]
    )

    print(f"\nJobs downloaded: {len(jobs)}")
    print(f"Role matches: {len(role_matches)}")
    print(f"Preferred location matches: {len(preferred_locations)}")
    print(f"Student / internship jobs excluded: {len(excluded)}")
    print(f"Remaining jobs: {len(suitable)}")

    for index, job in enumerate(suitable, start=1):
        print(f"\n--- Job {index} ---")
        print("Title:", job["title"])
        print("Company:", job["company"])
        print("Location:", job["location"])
        print("Seniority:", job["seniority"])
        print("Priority:", job["seniority_priority"])
        print("URL:", job["url"])


if __name__ == "__main__":
    main()