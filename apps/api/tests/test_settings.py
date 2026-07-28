from __future__ import annotations

import pytest

from job_os.settings import Settings

DB_URL = "postgresql+asyncpg://job_os:job_os@localhost/job_os"


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        # Gateway docs publish the versioned path; the SDK adds /v1 itself, so
        # keeping it would request /v1/v1/messages and 404 every call.
        ("https://app.manifest.build/v1", "https://app.manifest.build"),
        ("https://app.manifest.build/v1/", "https://app.manifest.build"),
        ("https://app.manifest.build", "https://app.manifest.build"),
        ("https://app.manifest.build/", "https://app.manifest.build"),
        # A gateway mounted under a prefix keeps the prefix, loses only /v1.
        ("https://gateway.example/api/v1", "https://gateway.example/api"),
        (None, None),
        ("", None),
    ],
)
def test_anthropic_base_url_drops_the_version_suffix(
    configured: str | None, expected: str | None
) -> None:
    settings = Settings(database_url=DB_URL, anthropic_base_url=configured)
    assert settings.anthropic_base_url == expected
