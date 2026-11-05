from __future__ import annotations

import pytest
from pydantic import ValidationError

from job_os.settings import Settings

DB_URL = "postgresql+asyncpg://job_os:job_os@localhost/job_os"


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        ("https://api.openai.com/v1", "https://api.openai.com/v1"),
        ("https://api.openai.com/v1/", "https://api.openai.com/v1"),
        # Pasted with the endpoint on it: llm_client appends that itself.
        ("https://api.openai.com/v1/chat/completions", "https://api.openai.com/v1"),
        ("  https://proxy.example/openai/v1/  ", "https://proxy.example/openai/v1"),
    ],
)
def test_openai_base_url_is_normalized(configured: str, expected: str) -> None:
    settings = Settings(database_url=DB_URL, openai_base_url=configured)
    assert settings.openai_base_url == expected


def test_openrouter_base_url_is_normalized() -> None:
    settings = Settings(
        database_url=DB_URL, openrouter_base_url="https://openrouter.ai/api/v1/chat/completions"
    )
    assert settings.openrouter_base_url == "https://openrouter.ai/api/v1"


def test_empty_llm_base_url_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(database_url=DB_URL, openai_base_url="  /  ")


def test_llm_defaults_go_to_providers_directly() -> None:
    settings = Settings(database_url=DB_URL)
    assert settings.openai_base_url == "https://api.openai.com/v1"
    assert settings.openrouter_base_url == "https://openrouter.ai/api/v1"
    assert settings.openai_model_quality == "gpt-6.1-terra"
    assert settings.openai_model_tailor == "gpt-6.1-terra"
    assert settings.openai_model_fast_fallback == "gpt-6-luna"
    assert settings.openrouter_model_fast == "nvidia/nemotron-3.5-lightning:free"


def test_no_gateway_settings_remain() -> None:
    fields = set(Settings.model_fields)
    leftovers = {name for name in fields if name.startswith(("anthropic_", "manifest_"))}
    assert leftovers == set()


def test_model_ids_and_keys_come_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test")
    monkeypatch.setenv("OPENAI_MODEL_TAILOR", "gpt-6.1-sol")
    settings = Settings(database_url=DB_URL)
    assert settings.openai_api_key == "sk-test"
    assert settings.openrouter_api_key == "or-test"
    assert settings.openai_model_tailor == "gpt-6.1-sol"
