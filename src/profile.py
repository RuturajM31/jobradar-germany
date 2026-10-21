"""
Day 11 — JobRadar Germany
Module: CV Profile Reader

THEORY:
AI agents need structured information to make decisions.

This module:
1. Reads the CV from a private local PDF.
2. Finds known professional skills and experience areas.
3. Saves a small structured profile.

PRIVACY:
- The complete CV never leaves this computer.
- No contact information is added to the profile.
- Only approved skill and experience labels are saved.
- Nothing is sent to Ollama Cloud in this module.
"""

import json
import re
from pathlib import Path

from pypdf import PdfReader


# Find our project folder, regardless of terminal location.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Private local files. These must never enter GitHub.
CV_PATH = PROJECT_ROOT / "data" / "private" / "resume.pdf"
PROFILE_PATH = PROJECT_ROOT / "data" / "private" / "professional_profile.json"


# THEORY: An allowlist contains only values we explicitly permit.
# This prevents names, email addresses, and phone numbers from
# being copied into the generated professional profile.

SKILL_CATEGORIES = {
    "Programming and Databases": [
        "SQL", "Python", "pandas", "NumPy",
        "scikit-learn", "DAX", "MySQL"
    ],
    "Business Intelligence": [
        "Power BI", "Power Query", "Tableau",
        "Looker Studio", "Excel", "KPI Reporting"
    ],
    "Data Engineering": [
        "ETL", "ELT", "Data Modelling",
        "Data Warehousing", "Data Validation",
        "Data Quality", "GCP", "Apache Druid",
        "SAP Business One"
    ],
    "Analytics": [
        "Predictive Analytics", "Machine Learning",
        "Forecasting", "Requirements Analysis"
    ],
}

EXPERIENCE_AREAS = [
    "Marketing", "Inventory", "Procurement",
    "Supply Chain", "Product", "ERP",
    "Business Intelligence", "Reporting"
]


def read_cv() -> str:
    """Read text from the locally stored CV PDF."""

    if not CV_PATH.is_file():
        raise FileNotFoundError(
            "CV not found. Save your PDF as data/private/resume.pdf"
        )

    reader = PdfReader(str(CV_PATH))

    # Read PDF pages into memory. Do not save the raw text.
    text = "\n".join(page.extract_text() or "" for page in reader.pages)

    if not text.strip():
        raise ValueError("No readable text found in the PDF.")

    return text


def find_keywords(text: str, keywords: list[str]) -> list[str]:
    """Find approved keywords appearing in the CV text."""

    found = []

    for keyword in keywords:
        # Match complete terms instead of unrelated word fragments.
        pattern = r"(?<!\w)" + re.escape(keyword) + r"(?!\w)"

        if re.search(pattern, text, flags=re.IGNORECASE):
            found.append(keyword)

    return found


def build_profile(cv_text: str) -> dict:
    """Create a professional profile containing approved labels only."""

    skills = {}

    for category, keywords in SKILL_CATEGORIES.items():
        skills[category] = find_keywords(cv_text, keywords)

    return {
        "skills": skills,
        "experience_areas": find_keywords(
            cv_text, EXPERIENCE_AREAS
        ),
        "note": (
            "Keywords detected in the CV. "
            "They require review before use for job matching."
        ),
    }


def main():
    """Read the CV, build its profile, and save locally."""

    cv_text = read_cv()
    profile = build_profile(cv_text)

    # Save only approved professional labels, not the CV text.
    PROFILE_PATH.write_text(
        json.dumps(profile, indent=4, ensure_ascii=False),
        encoding="utf-8"
    )

    total_skills = sum(
        len(items) for items in profile["skills"].values()
    )

    print("PASS: CV processed locally.")
    print(f"Professional skills detected: {total_skills}")
    print(f"Experience areas: {len(profile['experience_areas'])}")
    print("Private professional profile created.")


if __name__ == "__main__":
    main()