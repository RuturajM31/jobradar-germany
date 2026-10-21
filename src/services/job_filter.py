"""
Day 11 — JobRadar Germany
Module: Job Role Filter

THEORY:
Not every task requires an AI agent.

Python can perform predictable tasks, such as
checking whether a job title matches our target roles.

WHAT THIS MODULE DOES:
1. Loads job titles from job_roles.yaml.
2. Fetches real vacancies using the Arbeitnow module.
3. Compares each vacancy title with our target roles.
4. Groups matching jobs by priority.
5. Displays the matching vacancies.

LIMITATIONS:
- This is only a first-stage title filter.
- It does not yet check location, language or CV fit.
- The AI agents will perform deeper analysis later.
"""

import re
from pathlib import Path

import yaml

from src.sources.arbeitnow import fetch_jobs


# Find the project root folder.
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Our existing job-role configuration.
ROLES_PATH = PROJECT_ROOT / "config" / "job_roles.yaml"


def load_roles() -> dict:
    """Read the target job titles from our YAML configuration."""

    with ROLES_PATH.open("r", encoding="utf-8") as file:
        config = yaml.safe_load(file)

    return config["roles"]


def normalize_text(text: str) -> str:
    """
    Prepare text for comparison.

    Example:
        "Senior Data-Analyst (m/w/d)"
    becomes:
        "senior data analyst m w d"
    """

    text = text.casefold()

    # Replace punctuation with spaces.
    text = re.sub(r"[\W_]+", " ", text)

    return " ".join(text.split())


def match_job_title(title: str, roles: dict) -> dict | None:
    """
    Check whether a job title matches any configured role.

    Priority A is checked before B and C.

    Returns matching role details or None.
    """

    normalized_title = normalize_text(title)

    for priority in ["priority_a", "priority_b", "priority_c"]:

        for main_role, alternatives in roles[priority].items():

            search_terms = [main_role] + alternatives

            for term in search_terms:

                normalized_term = normalize_text(term)

                # Match complete words or phrases.
                if f" {normalized_term} " in f" {normalized_title} ":

                    return {
                        "priority": priority,
                        "matched_role": main_role,
                        "matched_term": term,
                    }

    return None


def filter_jobs(jobs: list[dict], roles: dict) -> list[dict]:
    """Return matching vacancies without duplicate URLs."""

    matches = []
    seen_urls = set()

    for job in jobs:

        title = job.get("title") or ""
        url = job.get("url") or ""

        # Ignore listings without a usable title or URL.
        if not title.strip() or not url.strip():
            continue

        # Avoid repeated URLs.
        if url in seen_urls:
            continue

        match = match_job_title(title, roles)

        if match is None:
            continue

        seen_urls.add(url)

        matches.append({
            "title": title,
            "company": job.get("company_name", "Unknown"),
            "location": job.get("location", "Unknown"),
            "remote": job.get("remote", False),
            "url": url,
            **match,
        })

    return matches


def main():
    """Fetch jobs and display the initial matching results."""

    print("JobRadar Germany — Job Role Filtering")
    print("-------------------------------------")

    roles = load_roles()

    print("Downloading vacancies...")

    try:
        jobs = fetch_jobs()

    except Exception as error:
        print(f"ERROR: Could not retrieve jobs: {error}")
        raise SystemExit(1) from error

    matches = filter_jobs(jobs, roles)

    print(f"\nJobs downloaded: {len(jobs)}")
    print(f"Role matches found: {len(matches)}")

    # Display match counts by priority.
    for priority in ["priority_a", "priority_b", "priority_c"]:

        count = sum(
            job["priority"] == priority
            for job in matches
        )

        print(f"{priority}: {count}")

    # Show the first 10 matching jobs.
    for index, job in enumerate(matches[:10], start=1):

        print(f"\n--- Matching Job {index} ---")
        print("Title:", job["title"])
        print("Company:", job["company"])
        print("Location:", job["location"])
        print("Priority:", job["priority"])
        print("Matched role:", job["matched_role"])
        print("URL:", job["url"])


if __name__ == "__main__":
    main()