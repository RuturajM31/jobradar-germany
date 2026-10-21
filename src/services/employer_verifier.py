"""
Day 11 — JobRadar Germany
Module: Employer Application Verifier

THEORY:
A listing on a job aggregator does not prove that an employer
still publishes the vacancy.

Applicant Tracking Systems (ATS), including Greenhouse, Lever
and Ashby, provide public APIs for published job advertisements.

This module checks an exact ATS job posting.

IMPORTANT:
- Never automatically submit applications.
- Never transmit the user's CV.
- Never treat HTTP 200 alone as proof of an active vacancy.
- Unknown employers and unsupported URLs remain unverified.
- ATS publication does not prove the job is less than 30 days old.
"""

import argparse
import re
from urllib.parse import urlsplit

import requests


# Public API requests need no personal information.
HEADERS = {
    "Accept": "application/json",
    "User-Agent": "JobRadarGermany/1.0",
}

TIMEOUT = 20


def ats_identity(
    job_url: str,
) -> tuple[str, str, str, str] | None:
    """
    Identify a supported recruitment platform.

    Returns:
        provider
        employer board
        posting ID
        API URL

    SECURITY:
    API requests are constructed only for approved
    ATS providers. We do not request arbitrary URLs.
    """

    if not isinstance(job_url, str):
        return None

    parsed = urlsplit(job_url.strip())

    if parsed.scheme != "https" or not parsed.hostname:
        return None

    host = parsed.hostname.lower()

    parts = [
        segment
        for segment in parsed.path.strip("/").split("/")
        if segment
    ]

    if len(parts) < 2:
        return None

    board = parts[0]

    if not re.fullmatch(
        r"[A-Za-z0-9_-]{2,100}",
        board,
    ):
        return None

    # GREENHOUSE
    if host in {
        "boards.greenhouse.io",
        "job-boards.greenhouse.io",
    }:

        if len(parts) != 3 or parts[1] != "jobs":
            return None

        posting_id = parts[2]

        if not posting_id.isdigit():
            return None

        api_url = (
            "https://boards-api.greenhouse.io"
            f"/v1/boards/{board}/jobs/{posting_id}"
        )

        return (
            "greenhouse",
            board,
            posting_id,
            api_url,
        )

    # LEVER
    if host in {
        "jobs.lever.co",
        "jobs.eu.lever.co",
    }:

        if len(parts) != 2:
            return None

        posting_id = parts[1]

        if not re.fullmatch(
            r"[A-Za-z0-9-]{8,100}",
            posting_id,
        ):
            return None

        api_host = (
            "api.eu.lever.co"
            if host == "jobs.eu.lever.co"
            else "api.lever.co"
        )

        api_url = (
            f"https://{api_host}/v0/postings/"
            f"{board}/{posting_id}"
        )

        return (
            "lever",
            board,
            posting_id,
            api_url,
        )

    # ASHBY
    if host == "jobs.ashbyhq.com":

        if len(parts) != 2:
            return None

        posting_id = parts[1]

        if not re.fullmatch(
            r"[A-Za-z0-9_-]{8,100}",
            posting_id,
        ):
            return None

        api_url = (
            "https://api.ashbyhq.com"
            f"/posting-api/job-board/{board}"
        )

        return (
            "ashby",
            board,
            posting_id,
            api_url,
        )

    return None


def title_matches(
    expected: str | None,
    actual: str,
) -> bool:
    """
    Verify that the returned job title is compatible.

    Example:
        Expected: Data Analyst
        Actual: Senior Data Analyst

    This is a basic safety check, not CV matching.
    """

    if not expected:
        return True

    def tokens(value: str) -> set[str]:
        """Normalize a title into comparable words."""

        return set(
            re.findall(
                r"[a-z0-9]+",
                value.casefold(),
            )
        )

    expected_tokens = tokens(expected)
    actual_tokens = tokens(actual)

    return bool(expected_tokens) and (
        expected_tokens <= actual_tokens
        or actual_tokens <= expected_tokens
    )


def result(
    status: str,
    provider: str | None,
    explanation: str,
    *,
    title: str | None = None,
    application_url: str | None = None,
    published_at: str | None = None,
    employment_type: str | None = None,
) -> dict:
    """
    Produce a standard result regardless of ATS provider.

    This makes it easier to integrate with JobRadar.
    """

    return {
        "status": status,
        "provider": provider,
        "explanation": explanation,
        "title": title,
        "application_url": application_url,
        "published_at": published_at,
        "employment_type": employment_type,
    }


def verify_employer_posting(
    job_url: str,
    expected_title: str | None = None,
    *,
    http_client=requests,
) -> dict:
    """
    Verify an employer's publicly published ATS job.

    Possible statuses:

    live_on_ats:
        The exact vacancy appears in the current
        public recruitment-system data.

    not_listed:
        The vacancy is not found on the current board.

    review:
        The response is uncertain or inconsistent.

    needs_employer_url:
        The supplied URL is not a supported direct
        employer ATS job URL.

    IMPORTANT:
    live_on_ats does NOT automatically mean that
    the position meets our 30-day freshness requirement.
    """

    identity = ats_identity(job_url)

    if identity is None:

        return result(
            "needs_employer_url",
            None,
            (
                "Not a supported direct Greenhouse, "
                "Lever or Ashby posting URL."
            ),
        )

    provider, board, posting_id, api_url = identity

    try:

        # Fetch only from our approved ATS API endpoint.
        response = http_client.get(
            api_url,
            headers=HEADERS,
            timeout=TIMEOUT,
            allow_redirects=False,
        )

        # A missing public posting is not eligible
        # for an active-job recommendation.
        if response.status_code == 404:

            return result(
                "not_listed",
                provider,
                (
                    "The current public ATS endpoint "
                    "did not find this job."
                ),
            )

        if response.status_code != 200:

            return result(
                "review",
                provider,
                (
                    "ATS endpoint returned HTTP "
                    f"{response.status_code}."
                ),
            )

        data = response.json()

        if not isinstance(data, dict):

            raise ValueError(
                "ATS response was not a JSON object"
            )

        # =========================================
        # GREENHOUSE VERIFICATION
        # =========================================

        if provider == "greenhouse":

            # Verify that the returned ID matches.
            if str(data.get("id")) != posting_id:

                return result(
                    "review",
                    provider,
                    "Greenhouse returned a different job ID.",
                )

            title = data.get("title")

            # Greenhouse provides the public job page.
            posting_url = data.get("absolute_url")

            if (
                not isinstance(posting_url, str)
                or ats_identity(posting_url) is None
            ):

                return result(
                    "review",
                    provider,
                    (
                        "Greenhouse did not provide "
                        "a usable public posting URL."
                    ),
                )

            # This is the public posting URL;
            # it may not be the direct application form.
            apply_url = posting_url

            published_at = None
            employment_type = None

        # =========================================
        # LEVER VERIFICATION
        # =========================================

        elif provider == "lever":

            if str(data.get("id")) != posting_id:

                return result(
                    "review",
                    provider,
                    "Lever returned a different posting ID.",
                )

            title = data.get("text")

            # Lever provides an application URL.
            apply_url = data.get("applyUrl")

            if (
                not isinstance(apply_url, str)
                or urlsplit(apply_url).hostname not in {
                    "jobs.lever.co",
                    "jobs.eu.lever.co",
                }
            ):

                return result(
                    "review",
                    provider,
                    (
                        "Lever did not provide a valid "
                        "hosted application URL."
                    ),
                )

            # Ensure the application link points
            # to the expected vacancy.
            if not apply_url.startswith(
                job_url.rstrip("/")
            ):

                return result(
                    "review",
                    provider,
                    (
                        "Lever application URL did "
                        "not match the posting."
                    ),
                )

            published_at = None

            categories = data.get("categories") or {}

            employment_type = (
                categories.get("commitment")
                if isinstance(categories, dict)
                else None
            )

        # =========================================
        # ASHBY VERIFICATION
        # =========================================

        else:

            jobs = data.get("jobs")

            if not isinstance(jobs, list):

                raise ValueError(
                    "Ashby response has no valid jobs list"
                )

            # Find the exact vacancy in the company's
            # currently published job-board data.
            matches = [
                item
                for item in jobs
                if (
                    isinstance(item, dict)
                    and isinstance(
                        item.get("jobUrl"),
                        str,
                    )
                    and urlsplit(
                        item["jobUrl"]
                    ).path.rstrip("/")
                    == urlsplit(
                        job_url
                    ).path.rstrip("/")
                )
            ]

            if not matches:

                return result(
                    "not_listed",
                    provider,
                    (
                        "The job is not in Ashby's "
                        "current published board feed."
                    ),
                )

            job = matches[0]

            if job.get("isListed") is not True:

                return result(
                    "review",
                    provider,
                    (
                        "Ashby does not mark this "
                        "job as publicly listed."
                    ),
                )

            title = job.get("title")
            apply_url = job.get("applyUrl")

            if (
                not isinstance(apply_url, str)
                or urlsplit(
                    apply_url
                ).hostname != "jobs.ashbyhq.com"
            ):

                return result(
                    "review",
                    provider,
                    (
                        "Ashby did not provide a "
                        "hosted application URL."
                    ),
                )

            published_at = job.get("publishedAt")
            employment_type = job.get("employmentType")

        # =========================================
        # FINAL IDENTITY CHECK
        # =========================================

        if (
            not isinstance(title, str)
            or not title_matches(
                expected_title,
                title,
            )
        ):

            return result(
                "review",
                provider,
                (
                    "Job title did not match "
                    "the expected vacancy."
                ),
            )

        # The employer's recruitment system
        # currently publishes this exact posting.
        #
        # We still need freshness and eligibility checks.
        return result(
            "live_on_ats",
            provider,
            (
                "Exact posting is currently published "
                "by the employer's ATS. "
                "No application was sent."
            ),
            title=title,
            application_url=apply_url,
            published_at=published_at,
            employment_type=employment_type,
        )

    except (
        requests.RequestException,
        ValueError,
    ) as error:

        return result(
            "review",
            provider,
            f"Could not verify employer ATS: {error}",
        )


def run_self_test() -> None:
    """
    Test the verifier without Internet access.

    THEORY:
    Mock API responses allow us to test each provider
    without contacting real employers.

    Tests:
    1. Greenhouse published vacancy
    2. Lever published vacancy
    3. Ashby published vacancy
    4. Vacancy missing from public ATS
    5. Incorrect vacancy title
    6. Unsupported aggregator URL
    """

    class Response:
        """Fake HTTP response for offline testing."""

        def __init__(
            self,
            data,
            status_code=200,
        ):

            self.data = data
            self.status_code = status_code

        def json(self):
            return self.data

    class Client:
        """Fake HTTP client to prevent real API calls."""

        def __init__(self, response):
            self.response = response

        def get(self, url, **kwargs):

            assert url.startswith("https://")

            assert kwargs["allow_redirects"] is False

            return self.response

    # TEST 1 — GREENHOUSE
    gh_url = (
        "https://job-boards.greenhouse.io/"
        "samplecompany/jobs/123456"
    )

    gh_data = {
        "id": 123456,
        "title": "Data Analyst",
        "absolute_url": gh_url,
    }

    gh_result = verify_employer_posting(
        gh_url,
        "Data Analyst",
        http_client=Client(
            Response(gh_data)
        ),
    )

    assert gh_result["status"] == "live_on_ats"

    print("PASS: Greenhouse live posting")

    # TEST 2 — LEVER
    lever_url = (
        "https://jobs.lever.co/samplecompany/"
        "12345678-1234-1234-1234-123456789abc"
    )

    lever_data = {
        "id": "12345678-1234-1234-1234-123456789abc",
        "text": "Business Analyst",
        "applyUrl": lever_url + "/apply",
        "categories": {
            "commitment": "Full-time",
        },
    }

    lever_result = verify_employer_posting(
        lever_url,
        "Business Analyst",
        http_client=Client(
            Response(lever_data)
        ),
    )

    assert lever_result["status"] == "live_on_ats"
    assert lever_result["employment_type"] == "Full-time"

    print("PASS: Lever live posting")

    # TEST 3 — ASHBY
    ashby_url = (
        "https://jobs.ashbyhq.com/samplecompany/"
        "12345678-1234-1234-1234-123456789abc"
    )

    ashby_data = {
        "jobs": [
            {
                "jobUrl": ashby_url,
                "title": "BI Analyst",
                "isListed": True,
                "applyUrl": ashby_url + "/application",
                "publishedAt": "2026-10-19",
                "employmentType": "FullTime",
            }
        ]
    }

    ashby_result = verify_employer_posting(
        ashby_url,
        "BI Analyst",
        http_client=Client(
            Response(ashby_data)
        ),
    )

    assert ashby_result["status"] == "live_on_ats"
    assert ashby_result["employment_type"] == "FullTime"

    print("PASS: Ashby live posting")

    # TEST 4 — JOB NOT FOUND
    not_found = verify_employer_posting(
        gh_url,
        http_client=Client(
            Response({}, 404)
        ),
    )

    assert not_found["status"] == "not_listed"

    print("PASS: ATS posting not found")

    # TEST 5 — WRONG JOB TITLE
    mismatch = verify_employer_posting(
        gh_url,
        "Marketing Analyst",
        http_client=Client(
            Response(gh_data)
        ),
    )

    assert mismatch["status"] == "review"

    print("PASS: Mismatched title held for review")

    # TEST 6 — AGGREGATOR URL
    aggregator_url = (
        "https://www.arbeitsagentur.de/"
        "jobsuche/jobdetail/TEST-001"
    )

    unsupported = verify_employer_posting(
        aggregator_url
    )

    assert (
        unsupported["status"]
        == "needs_employer_url"
    )

    print(
        "PASS: Aggregator URL cannot prove "
        "employer-side status"
    )

    print(
        "PASS: All employer-verification "
        "tests completed"
    )


def main() -> None:
    """
    Command-line interface.

    --self-test:
        Run six tests without network calls.

    --url:
        Check a supported employer ATS job URL.

    --title:
        Optionally verify the expected job title.
    """

    parser = argparse.ArgumentParser(
        description="JobRadar Employer ATS Verification"
    )

    parser.add_argument(
        "--self-test",
        action="store_true",
    )

    parser.add_argument(
        "--url",
        help="Direct Greenhouse, Lever or Ashby job URL",
    )

    parser.add_argument(
        "--title",
        help="Expected vacancy title",
    )

    args = parser.parse_args()

    if args.self_test:

        run_self_test()

    elif args.url:

        outcome = verify_employer_posting(
            args.url,
            args.title,
        )

        for key, value in outcome.items():
            print(f"{key}: {value}")

    else:

        parser.print_help()


if __name__ == "__main__":
    main()