"""
Day 11 — JobRadar Germany
Module: Ollama Cloud LLM Configuration

THEORY
------
CrewAI agents need an LLM, but constructing an LLM
is not the same as executing it.

Ollama Cloud exposes an OpenAI-compatible endpoint.

CrewAI can connect through custom_openai=True
without sending requests to OpenAI.

PRIVACY
-------
- API keys are read from environment variables.
- The key is never written into Python source code.
- No private CV information is transmitted.
- No network request occurs during the offline self-test.
- Live requests require an explicit command.
"""

import argparse
import os
import re

from urllib.parse import urlsplit

import requests

from crewai import LLM


# ============================================================
# CONFIGURATION
# ============================================================

# Official Ollama Cloud OpenAI-compatible endpoint.
OLLAMA_BASE_URL = "https://ollama.com/v1"

# A smaller cloud model helps conserve free-tier usage.
DEFAULT_MODEL = "gpt-oss:20b"


# ============================================================
# STEP 1 — VALIDATE MODEL CONFIGURATION
# ============================================================

def configured_model() -> str:
    """
    Return a valid Ollama Cloud model ID.

    The default can be changed using the
    OLLAMA_CLOUD_MODEL environment variable.

    We never substitute a paid provider automatically.
    """

    model = os.environ.get(
        "OLLAMA_CLOUD_MODEL",
        DEFAULT_MODEL,
    ).strip()

    if not re.fullmatch(
        r"[A-Za-z0-9_.:-]{2,100}",
        model,
    ):

        raise ValueError(
            "Invalid OLLAMA_CLOUD_MODEL setting."
        )

    return model


# ============================================================
# STEP 2 — LOAD API KEY SECURELY
# ============================================================

def require_api_key() -> str:
    """
    Read the Ollama API key from the environment.

    THEORY:
    Environment variables keep credentials separate
    from application source code.

    Never include the key in:
    - Python files
    - Git repositories
    - Job reports
    - Console logs
    """

    key = os.environ.get(
        "OLLAMA_API_KEY",
        "",
    ).strip()

    if not key:

        raise RuntimeError(
            "OLLAMA_API_KEY is missing. "
            "Set it in PowerShell before "
            "testing Ollama Cloud."
        )

    return key


# ============================================================
# STEP 3 — CREATE CREWAI LANGUAGE MODEL
# ============================================================

def build_ollama_llm() -> LLM:
    """
    Construct a CrewAI LLM using Ollama Cloud.

    THEORY:
    custom_openai=True instructs CrewAI to use
    an OpenAI-compatible API.

    This does NOT mean requests go to OpenAI.

    Our base_url explicitly points to Ollama Cloud.

    The exact model ID is passed to Ollama.

    IMPORTANT:
    Creating this object does not execute an agent.
    """

    return LLM(
        model=configured_model(),

        # Use the native OpenAI-compatible connection.
        custom_openai=True,

        # Never use the default OpenAI endpoint.
        base_url=OLLAMA_BASE_URL,

        # Credential supplied only through environment.
        api_key=require_api_key(),

        # Lower randomness for evidence-based analysis.
        temperature=0.1,

        # Limit output tokens to reduce usage.
        max_tokens=512,

        # Bound request time.
        timeout=60,

        # Prevent automatic repeated failed requests.
        max_retries=0,
    )


# ============================================================
# STEP 4 — TEST THE ACTUAL CLOUD CONNECTION
# ============================================================

def check_cloud_connection(
    http_client=requests,
) -> None:
    """
    Make exactly one small cloud API request.

    This test deliberately sends a fixed sentence.

    It does NOT send:
    - CV data
    - Professional profile
    - Contact information
    - Job descriptions

    Network access occurs only when
    --check-cloud is explicitly requested.
    """

    model = configured_model()

    secret = require_api_key()

    endpoint = (
        OLLAMA_BASE_URL
        + "/chat/completions"
    )

    try:

        response = http_client.post(
            endpoint,

            headers={
                "Authorization": (
                    f"Bearer {secret}"
                ),
                "Content-Type": "application/json",
            },

            json={
                "model": model,

                "messages": [
                    {
                        "role": "user",
                        "content": (
                            "Reply with the single word READY."
                        ),
                    }
                ],

                "stream": False,

                "max_tokens": 128,
            },

            timeout=45,

            # Do not send credentials to a redirect.
            allow_redirects=False,
        )

    except requests.RequestException as error:

        raise RuntimeError(
            "Could not reach Ollama Cloud. "
            "Check your Internet connection."
        ) from error

    # Do not log API keys, headers or request bodies.
    if response.status_code != 200:

        raise RuntimeError(
            "Ollama Cloud returned HTTP "
            f"{response.status_code}. "
            "Check API key, model access "
            "and free-tier usage."
        )

    try:

        payload = response.json()

        choices = payload.get(
            "choices",
            [],
        )

        if (
            not isinstance(choices, list)
            or not choices
        ):

            raise ValueError(
                "No completion choices returned."
            )

        message = choices[0].get(
            "message",
            {},
        )

        if not isinstance(message, dict):

            raise ValueError(
                "Invalid completion message."
            )

    except (
        ValueError,
        TypeError,
        IndexError,
        AttributeError,
    ) as error:

        raise RuntimeError(
            "Unexpected Ollama Cloud response."
        ) from error

    print(
        f"PASS: Ollama Cloud responded "
        f"(model: {model})"
    )

    print(
        "PASS: Connection test used only "
        "a fixed, nonprivate message"
    )

    print(
        "No CV or job data was transmitted."
    )


# ============================================================
# STEP 5 — BUILD OUR EXISTING CREWAI TEAM
# ============================================================

def inspect_crew() -> None:
    """
    Construct the real hierarchical CrewAI objects.

    Connects the model to:
    - Recruitment Manager
    - Job Researcher
    - Job Validator
    - CV Match Analyst
    - Career Advisor

    IMPORTANT:
    We do not call crew.kickoff() here.
    No agents execute during this inspection.
    """

    from src.crew.workflow import (
        build_jobradar_crew,
    )

    llm = build_ollama_llm()

    crew = build_jobradar_crew(
        llm
    )

    # The manager is configured separately.
    assert len(crew.agents) == 4

    # Four specialist tasks.
    assert len(crew.tasks) == 4

    # Manager exists.
    assert crew.manager_agent is not None

    print(
        "PASS: Real CrewAI objects constructed"
    )

    print(
        "PASS: Four workers, four tasks, "
        "one Recruitment Manager"
    )

    print(
        "No crew tasks were executed."
    )


# ============================================================
# STEP 6 — OFFLINE TESTS
# ============================================================

def run_self_test() -> None:
    """
    Validate Ollama configuration without API access.

    We test:
    1. Cloud endpoint
    2. Default model identifier
    3. Provider endpoint isolation
    4. Offline operation without credentials
    """

    assert (
        OLLAMA_BASE_URL
        == "https://ollama.com/v1"
    )

    assert (
        urlsplit(
            OLLAMA_BASE_URL
        ).hostname
        == "ollama.com"
    )

    print(
        "PASS: Ollama Cloud endpoint is fixed"
    )

    assert re.fullmatch(
        r"[A-Za-z0-9_.:-]{2,100}",
        DEFAULT_MODEL,
    )

    print(
        "PASS: Default model name is valid"
    )

    assert (
        urlsplit(
            OLLAMA_BASE_URL
        ).hostname
        != "api.openai.com"
    )

    print(
        "PASS: Endpoint is not OpenAI's API"
    )

    print(
        "PASS: Offline test does not require "
        "a cloud API key"
    )

    print(
        "PASS: No AI or job API calls were made"
    )


# ============================================================
# STEP 7 — COMMAND-LINE INTERFACE
# ============================================================

def main() -> None:
    """
    Available commands:

    --self-test
        Offline configuration tests.

    --check-cloud
        Send one small test message to Ollama.

    --build-crew
        Construct the real CrewAI objects
        without executing the tasks.
    """

    parser = argparse.ArgumentParser(
        description="JobRadar Ollama Cloud setup"
    )

    options = (
        parser.add_mutually_exclusive_group(
            required=True
        )
    )

    options.add_argument(
        "--self-test",
        action="store_true",
    )

    options.add_argument(
        "--check-cloud",
        action="store_true",
    )

    options.add_argument(
        "--build-crew",
        action="store_true",
    )

    args = parser.parse_args()

    try:

        if args.self_test:

            run_self_test()

        elif args.check_cloud:

            check_cloud_connection()

        else:

            inspect_crew()

    except (
        RuntimeError,
        ValueError,
    ) as error:

        parser.exit(
            1,
            f"ERROR: {error}\n",
        )


if __name__ == "__main__":
    main()