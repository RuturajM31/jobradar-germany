"""
Day 11 — JobRadar Germany
Module: Arbeitnow Job API (Paginated)

THEORY:
An API allows our application to request external data.

PAGINATION:
Some APIs divide results into multiple pages.
To retrieve more jobs, we request additional pages.

WHAT THIS MODULE DOES:
1. Connects to the free Arbeitnow API.
2. Downloads up to three pages.
3. Stops when no more pages are available.
4. Removes duplicate job URLs.
5. Returns the collected vacancies.

PRIVACY:
No CV or personal information is sent to Arbeitnow.
"""

import requests


API_URL = "https://www.arbeitnow.com/api/job-board-api"

# Limit requests to avoid unnecessary API usage.
MAX_PAGES = 3


def fetch_jobs(max_pages: int = MAX_PAGES) -> list[dict]:
    """
    Download vacancies from multiple API pages.

    Args:
        max_pages: Maximum number of pages to request.

    Returns:
        A list of job dictionaries without duplicate URLs.
    """

    if max_pages < 1:
        raise ValueError("max_pages must be at least 1.")

    all_jobs = []
    seen_urls = set()

    for page in range(1, max_pages + 1):

        print(f"Downloading page {page}...")

        response = requests.get(
            API_URL,
            params={"page": page},
            timeout=25,
            headers={"User-Agent": "JobRadarGermany/1.0"},
        )

        response.raise_for_status()

        payload = response.json()

        if not isinstance(payload, dict):
            raise ValueError("Unexpected API response.")

        jobs = payload.get("data")

        if not isinstance(jobs, list):
            raise ValueError("Invalid job list.")

        if not jobs:
            print("No more jobs available.")
            break

        new_count = 0

        for job in jobs:

            if not isinstance(job, dict):
                continue

            url = job.get("url")

            if not isinstance(url, str) or not url.strip():
                continue

            # Normalize URLs to detect repeated listings.
            normalized_url = url.strip().rstrip("/").casefold()

            if normalized_url in seen_urls:
                continue

            seen_urls.add(normalized_url)
            all_jobs.append(job)
            new_count += 1

        print(f"New jobs collected: {new_count}")

        # Stop if the page contains no new advertisements.
        if new_count == 0:
            break

        # Use the API's pagination information.
        links = payload.get("links") or {}

        if not isinstance(links, dict) or not links.get("next"):
            print("Last available page reached.")
            break

    return all_jobs


def main():
    """Test the paginated API and display a summary."""

    print("JobRadar Germany — Extended Job Discovery")
    print("-----------------------------------------")

    try:
        jobs = fetch_jobs()

    except (requests.RequestException, ValueError) as error:
        print(f"ERROR: {error}")
        raise SystemExit(1) from error

    print("\nPASS: Job retrieval completed.")
    print(f"Total unique jobs: {len(jobs)}")

    # Display only five jobs for a short terminal output.
    for index, job in enumerate(jobs[:5], start=1):

        print(f"\n--- Job {index} ---")
        print("Title:", job.get("title", "Unknown"))
        print("Company:", job.get("company_name", "Unknown"))
        print("Location:", job.get("location", "Unknown"))
        print("URL:", job.get("url", "Unknown"))


if __name__ == "__main__":
    main()