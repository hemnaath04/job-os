"""Provider registry.

Adding an ATS vendor means adding a module here and one line to `PROVIDERS`.
Nothing downstream of `RawPosting` knows which vendor a row came from, so the
upsert, dedupe and read path do not change.
"""
from __future__ import annotations

from job_os.ingest.providers.ashby import AshbyProvider
from job_os.ingest.providers.base import (
    ESTIMATED_BASES,
    POSTED_AT_BASES,
    BoardResult,
    BoardStatus,
    Provider,
    RawPosting,
)
from job_os.ingest.providers.greenhouse import GreenhouseProvider
from job_os.ingest.providers.lever import LeverProvider
from job_os.ingest.providers.smartrecruiters import SmartRecruitersProvider

PROVIDERS: dict[str, Provider] = {
    GreenhouseProvider.name: GreenhouseProvider(),
    LeverProvider.name: LeverProvider(),
    AshbyProvider.name: AshbyProvider(),
    SmartRecruitersProvider.name: SmartRecruitersProvider(),
}

PROVIDER_NAMES = tuple(PROVIDERS)


def get_provider(name: str) -> Provider:
    try:
        return PROVIDERS[name]
    except KeyError:
        raise ValueError(
            f"unknown provider {name!r}; known: {', '.join(PROVIDER_NAMES)}"
        ) from None


__all__ = [
    "ESTIMATED_BASES",
    "POSTED_AT_BASES",
    "PROVIDERS",
    "PROVIDER_NAMES",
    "AshbyProvider",
    "BoardResult",
    "BoardStatus",
    "GreenhouseProvider",
    "LeverProvider",
    "Provider",
    "RawPosting",
    "SmartRecruitersProvider",
    "get_provider",
]
