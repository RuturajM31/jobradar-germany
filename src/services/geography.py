"""
Day 11 — JobRadar Germany
Module: Geography Validator

THEORY:
Data validation checks whether information satisfies
rules before it enters an AI decision-making process.

A job can match our skills but still be unsuitable
because of its location.

This module distinguishes:
- Preferred cities
- Nearby towns
- Other German locations
- Remote jobs available in Germany
- Clearly foreign jobs
- Remote jobs requiring eligibility verification
- Uncertain locations

IMPORTANT:
A town being classified as nearby does not establish
an exact commuting time.

This module does not make network requests.
"""

import re
import unicodedata
from urllib.parse import urlsplit

from src.services.location_filter import load_location_settings


# Additional towns in the Munich and Augsburg regions.
# These are candidate search areas, not guaranteed commutes.
ADDITIONAL_NEARBY_TOWNS = [
    "Ottobrunn",
    "Ismaning",
    "Unterföhring",
    "Garching",
    "Unterschleißheim",
    "Aschheim",
    "Haar",
    "Planegg",
    "Gräfelfing",
    "Germering",
    "Starnberg",
    "Olching",
    "Stadtbergen",
    "Bobingen",
    "Mering",
    "Diedorf",
    "Aichach",
]

# Foreign countries and cities appearing in international
# job feeds. This list can be expanded over time.
FOREIGN_LOCATIONS = [
    "Switzerland",
    "Schweiz",
    "Suisse",
    "France",
    "Frankreich",
    "United Kingdom",
    "England",
    "Scotland",
    "Netherlands",
    "Nederland",
    "Niederlande",
    "Austria",
    "Österreich",
    "Zurich",
    "Zürich",
    "Bern",
    "Solothurn",
    "Lausanne",
    "Lille",
    "Hatfield",
    "Zwolle",
    "Hertogenbosch",
]

# These sites primarily represent country-specific feeds.
FOREIGN_ARBEITNOW_HOSTS = [
    "www.arbeitnow.ch",
    "www.arbeitnow.fr",
    "www.arbeitnow.nl",
    "www.arbeitnow.co.uk",
]


def normalize_place(text: str) -> str:
    """
    Normalize spelling for geographic comparison.

    This removes differences caused by capital letters,
    accents, and punctuation.
    """

    text = text.casefold().replace("ß", "ss")

    text = unicodedata.normalize("NFKD", text)

    text = "".join(
        character
        for character in text
        if not unicodedata.combining(character)
    )

    text = re.sub(r"[^\w]+", " ", text)

    return " ".join(text.split())


def contains_location(text: str, places: list[str]) -> bool:
    """Find complete city or country names in location text."""

    normalized_text = normalize_place(text)

    for place in places:
        normalized_place = normalize_place(place)

        if not normalized_place:
            continue

        pattern = (
            r"(?<!\w)"
            + re.escape(normalized_place)
            + r"(?!\w)"
        )

        if re.search(pattern, normalized_text):
            return True

    return False


def is_foreign_feed(url: str) -> bool:
    """
    Identify a country-specific international job feed.

    The website alone is a signal, not absolute proof
    that employment in Germany is impossible.
    """

    hostname = urlsplit(url).hostname or ""

    return hostname.casefold() in FOREIGN_ARBEITNOW_HOSTS


def classify_geography(job: dict, settings: dict) -> str:
    """
    Classify a vacancy's geographic suitability.

    Returns one of:
        preferred_city
        nearby_area
        remote_germany
        other_germany
        outside_germany
        remote_eligibility_unconfirmed
        needs_review
    """

    location = str(job.get("location") or "")
    url = str(job.get("url") or "")
    source = str(job.get("source") or "")
    remote = job.get("remote") is True

    # Identify explicit German eligibility.
    german_country = contains_location(
        location,
        ["Germany", "Deutschland", "Bundesweit"],
    )

    # Detect evidence of a foreign location.
    foreign_location = contains_location(
        location,
        FOREIGN_LOCATIONS,
    )

    foreign_feed = is_foreign_feed(url)

    # Contradictory signals require manual verification.
    if foreign_location and german_country:
        return "needs_review"

    # A foreign remote listing might permit Germany,
    # but we cannot assume eligibility.
    if foreign_location or foreign_feed:

        if remote:
            return "remote_eligibility_unconfirmed"

        if not german_country:
            return "outside_germany"

    # Check explicitly German remote jobs.
    if remote and german_country:
        return "remote_germany"

    # Check our preferred cities.
    preferred_cities = list(settings["preferred_cities"])

    # Add the German and English Munich spellings.
    preferred_cities.extend(
        ["Munich", "München", "Muenchen"]
    )

    if contains_location(location, preferred_cities):
        return "preferred_city"

    # Check configured and additional nearby towns.
    nearby_towns = (
        list(settings["nearby_areas"])
        + ADDITIONAL_NEARBY_TOWNS
    )

    if contains_location(location, nearby_towns):
        return "nearby_area"

    # An explicitly German location is worth keeping,
    # even when it lies outside preferred search areas.
    if german_country:
        return "other_germany"

    # Federal Employment Agency listings concern the
    # German job market, but may be outside our commute.
    if source == "Bundesagentur für Arbeit":
        return "other_germany"

    # Unknown locations require further investigation.
    return "needs_review"


def main():
    """
    Test geographic classifications without calling APIs.

    Expected results are checked with assertions.
    """

    settings = load_location_settings()

    test_cases = [
        (
            {
                "location": "München",
                "remote": False,
                "source": "Bundesagentur für Arbeit",
                "url": "https://www.arbeitsagentur.de/jobsuche",
            },
            "preferred_city",
        ),
        (
            {
                "location": "Ottobrunn",
                "remote": False,
                "source": "Bundesagentur für Arbeit",
                "url": "https://www.arbeitsagentur.de/jobsuche",
            },
            "nearby_area",
        ),
        (
            {
                "location": "Ismaning",
                "remote": False,
                "source": "Bundesagentur für Arbeit",
                "url": "https://www.arbeitsagentur.de/jobsuche",
            },
            "nearby_area",
        ),
        (
            {
                "location": "Zurich, Switzerland",
                "remote": False,
                "source": "Arbeitnow",
                "url": "https://www.arbeitnow.ch/jobs/example",
            },
            "outside_germany",
        ),
        (
            {
                "location": "Lille",
                "remote": False,
                "source": "Arbeitnow",
                "url": "https://www.arbeitnow.fr/jobs/example",
            },
            "outside_germany",
        ),
        (
            {
                "location": "Remote - Germany",
                "remote": True,
                "source": "Arbeitnow",
                "url": "https://www.arbeitnow.com/jobs/example",
            },
            "remote_germany",
        ),
        (
            {
                "location": "France",
                "remote": True,
                "source": "Arbeitnow",
                "url": "https://www.arbeitnow.fr/jobs/example",
            },
            "remote_eligibility_unconfirmed",
        ),
    ]

    print("JobRadar Germany — Geography Validation")
    print("---------------------------------------")

    for job, expected in test_cases:
        actual = classify_geography(job, settings)

        print(
            f"{job['location']}: "
            f"{actual}"
        )

        assert actual == expected, (
            f"Expected {expected}, got {actual}"
        )

    print("\nPASS: All geography tests completed.")


if __name__ == "__main__":
    main()