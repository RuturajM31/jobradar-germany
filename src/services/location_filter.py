"""
Day 11 — JobRadar Germany
Module: Location Filter

THEORY:
Not every operation requires an AI agent.

Python handles predictable filtering rules.
CrewAI agents will handle more complex decisions later.

WHAT THIS MODULE DOES:
1. Loads our location preferences from YAML.
2. Retrieves vacancies matching our job roles.
3. Identifies preferred cities and nearby areas.
4. Identifies remote jobs explicitly marked for Germany.
5. Keeps uncertain locations separate for later review.

IMPORTANT:
A job marked "remote" is not automatically
eligible for someone working in Germany.
"""

import re
from pathlib import Path

import yaml

from src.sources.arbeitnow import fetch_jobs
from src.services.job_filter import load_roles, filter_jobs


# Find the main project directory.
PROJECT_ROOT = Path(__file__).resolve().parents[2]

FILTERS_PATH = PROJECT_ROOT / "config" / "search_filters.yaml"


def load_location_settings() -> dict:
    """Load the location preferences saved in YAML."""

    with FILTERS_PATH.open("r", encoding="utf-8") as file:
        return yaml.safe_load(file)["locations"]


def contains_place(location: str, place: str) -> bool:
    """Check whether a location contains a city name."""

    location = location.casefold()

    # Handle both English and German names for Munich.
    alternatives = [place]

    if place.casefold() == "munich":
        alternatives.append("München")

    for name in alternatives:
        pattern = r"(?<!\w)" + re.escape(name.casefold()) + r"(?!\w)"

        if re.search(pattern, location):
            return True

    return False


def classify_location(job: dict, settings: dict) -> str:
    """
    Classify a job based on our location preferences.

    Returns:
    - preferred_city
    - nearby_area
    - remote_germany
    - needs_review
    """

    location = str(job.get("location") or "")
    remote = job.get("remote") is True

    # First: Munich and Augsburg.
    for city in settings["preferred_cities"]:
        if contains_place(location, city):
            return "preferred_city"

    # Second: Nearby cities from our YAML configuration.
    for city in settings["nearby_areas"]:
        if contains_place(location, city):
            return "nearby_area"

    # Third: Remote roles explicitly mentioning Germany.
    germany_pattern = r"(?<!\w)(germany|deutschland|bundesweit)(?!\w)"

    if (
        settings["remote_germany"]
        and remote
        and re.search(germany_pattern, location, re.IGNORECASE)
    ):
        return "remote_germany"

    # Other German cities and unclear remote eligibility
    # remain available for further verification.
    return "needs_review"


def main():
    """Run job-role filtering followed by location filtering."""

    print("JobRadar Germany — Location Filtering")
    print("-------------------------------------")

    settings = load_location_settings()
    roles = load_roles()

    print("Downloading jobs...")

    jobs = fetch_jobs()

    # Apply our existing role filter first.
    matches = filter_jobs(jobs, roles)

    selected = []
    needs_review = []

    for job in matches:
        category = classify_location(job, settings)

        job["location_category"] = category

        if category == "needs_review":
            needs_review.append(job)
        else:
            selected.append(job)

    print(f"\nJobs downloaded: {len(jobs)}")
    print(f"Role matches: {len(matches)}")
    print(f"Preferred location matches: {len(selected)}")
    print(f"Other locations needing review: {len(needs_review)}")

    # Display only our preferred location matches.
    for index, job in enumerate(selected, start=1):
        print(f"\n--- Job {index} ---")
        print("Title:", job["title"])
        print("Company:", job["company"])
        print("Location:", job["location"])
        print("Category:", job["location_category"])
        print("Remote:", job["remote"])
        print("URL:", job["url"])

        # Flag student positions for later seniority filtering.
        if re.search(
            r"working student|werkstudent|internship|praktikum",
            job["title"],
            re.IGNORECASE,
        ):
            print("WARNING: Student/internship position")


if __name__ == "__main__":
    main()