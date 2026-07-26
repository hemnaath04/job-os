from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path

from appwrite.client import Client
from dotenv import dotenv_values

VALID_APPWRITE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,35}$")


@dataclass(frozen=True)
class AppwriteAdminConfig:
    endpoint: str
    project_id: str
    api_key: str
    database_id: str
    applications_table_id: str

    @classmethod
    def from_environment(cls) -> AppwriteAdminConfig:
        repo_root = Path(__file__).resolve().parents[5]
        file_values = {
            **dotenv_values(repo_root / ".env"),
            **dotenv_values(repo_root / ".env.local"),
        }

        def value(*names: str) -> str | None:
            for name in names:
                candidate = os.getenv(name) or file_values.get(name)
                if candidate:
                    return candidate
            return None

        project_id = value("APPWRITE_PROJECT_ID", "NEXT_PUBLIC_APPWRITE_PROJECT_ID")
        api_key = value("APPWRITE_API_KEY")
        if not project_id or not api_key:
            raise RuntimeError("Set APPWRITE_PROJECT_ID and APPWRITE_API_KEY.")
        return cls(
            endpoint=(
                value("APPWRITE_ENDPOINT", "NEXT_PUBLIC_APPWRITE_ENDPOINT")
                or "https://cloud.appwrite.io/v1"
            ),
            project_id=project_id,
            api_key=api_key,
            database_id=(
                value("APPWRITE_DATABASE_ID", "NEXT_PUBLIC_APPWRITE_DATABASE_ID")
                or "job-os"
            ),
            applications_table_id=(
                value(
                    "APPWRITE_APPLICATIONS_TABLE_ID",
                    "NEXT_PUBLIC_APPWRITE_APPLICATIONS_TABLE_ID",
                )
                or "application_cards"
            ),
        )

    def client(self) -> Client:
        return (
            Client()
            .set_endpoint(self.endpoint)
            .set_project(self.project_id)
            .set_key(self.api_key)
        )


def appwrite_user_id_for_clerk(clerk_user_id: str) -> str:
    if VALID_APPWRITE_ID.fullmatch(clerk_user_id):
        return clerk_user_id
    digest = hashlib.sha256(clerk_user_id.encode()).hexdigest()[:30]
    return f"clerk_{digest}"
