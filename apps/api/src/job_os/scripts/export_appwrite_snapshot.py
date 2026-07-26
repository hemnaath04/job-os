"""Export a read-only Neon snapshot for the staged Appwrite pipeline import."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import joinedload

from job_os.db.models import Application, Job, User
from job_os.db.session import async_session, engine
from job_os.schemas.applications import ApplicationRead


async def export_snapshot(output: Path) -> int:
    async with async_session() as session:
        result = await session.execute(
            select(Application, User)
            .join(User, Application.user_id == User.id)
            .options(joinedload(Application.job).joinedload(Job.company))
            .order_by(Application.id)
        )

        applications: list[dict[str, Any]] = []
        for application, user in result.unique().all():
            applications.append(
                {
                    "owner": {
                        "clerk_id": user.clerk_id,
                        "email": user.email,
                        "display_name": user.display_name,
                    },
                    "application": ApplicationRead.model_validate(application).model_dump(
                        mode="json"
                    ),
                }
            )

    payload = {
        "schema_version": 1,
        "exported_at": datetime.now(UTC).isoformat(),
        "applications": applications,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(
        output.write_text,
        json.dumps(payload, indent=2),
        encoding="utf-8",
    )
    await engine.dispose()
    return len(applications)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path, help="Absolute destination for the JSON snapshot")
    args = parser.parse_args()
    count = asyncio.run(export_snapshot(args.output.expanduser().resolve()))
    print(f"Exported {count} applications to {args.output}")  # noqa: T201


if __name__ == "__main__":
    main()
