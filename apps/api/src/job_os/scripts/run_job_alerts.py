"""Cron entrypoint for the job alert digest.

    uv run python -m job_os.scripts.run_job_alerts --dry-run
    uv run python -m job_os.scripts.run_job_alerts --dry-run --out /tmp/digest.txt
    uv run python -m job_os.scripts.run_job_alerts --send          # needs ALERTS_ENABLED=true

Dry run is the default and `--send` is the only way to turn it off. That is
deliberate: this file will eventually be invoked by a scheduler with whatever
arguments someone typed months earlier, and the version of the command that mails
strangers should not be the one you get by forgetting a flag.

`--send` still fails unless ALERTS_ENABLED is true, EMAIL_PROVIDER is a real
provider, and the unsubscribe secret and postal address are both configured. See
`alert_runner.require_send_config` and docs/ALERTS.md.

Nothing schedules this yet. .github/workflows/job-alerts.yml exists with
workflow_dispatch only and its cron commented out.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from job_os.db.session import async_session
from job_os.services.alert_runner import (
    AlertsNotConfiguredError,
    RunReport,
    run_alerts,
)

#: Exit codes. 0 clean, 1 a subscription failed, 2 the run could not start.
EXIT_OK = 0
EXIT_PARTIAL_FAILURE = 1
EXIT_NOT_CONFIGURED = 2


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="run_job_alerts",
        description="Build and optionally send job alert digests.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Render digests and send nothing. The default.",
    )
    mode.add_argument(
        "--send",
        action="store_true",
        help="Actually send. Requires ALERTS_ENABLED=true and a configured provider.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        help="Write the rendered dry-run digests here instead of stdout.",
    )
    parser.add_argument(
        "--html-out",
        type=Path,
        help="Write the HTML part of each dry-run digest here, for opening in a browser.",
    )
    parser.add_argument(
        "--subscription-id",
        help="Run one subscription instead of every due one.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Ignore the cadence and treat every subscription as due. Dedupe still applies.",
    )
    parser.add_argument(
        "--now",
        help="ISO 8601 instant to treat as the current time. For testing cadence boundaries.",
    )
    return parser.parse_args(argv)


def _render_report(report: RunReport) -> str:
    lines = [
        f"job.os alert run at {report.started_at.isoformat()}",
        f"mode: {'dry run, nothing sent' if report.dry_run else 'live send'}",
        report.summary_line(),
        "",
    ]
    for outcome in report.outcomes:
        lines.append(
            f"[{outcome.outcome}] {outcome.search_name} "
            f"(candidates={outcome.candidates} kept={outcome.job_count} "
            f"deduped={outcome.deduped_count} reposts={outcome.repost_count})"
        )
        if outcome.reason and outcome.outcome == "skipped_not_due":
            lines.append(f"    not due: {outcome.reason}")
        if outcome.error:
            lines.append(f"    error: {outcome.error}")
        if outcome.rendered_text:
            lines.append("")
            lines.append(f"    subject: {outcome.subject}")
            lines.append("    " + "-" * 68)
            lines.extend(f"    {line}" for line in outcome.rendered_text.splitlines())
            lines.append("    " + "-" * 68)
        lines.append("")
    return "\n".join(lines)


async def _run(args: argparse.Namespace) -> int:
    dry_run = not args.send
    now = datetime.fromisoformat(args.now).astimezone(UTC) if args.now else None
    subscription_id = UUID(args.subscription_id) if args.subscription_id else None

    async with async_session() as session:
        try:
            report = await run_alerts(
                session,
                now=now,
                dry_run=dry_run,
                subscription_id=subscription_id,
                force=args.force,
            )
        except AlertsNotConfiguredError as e:
            print(f"cannot send: {e}", file=sys.stderr)
            return EXIT_NOT_CONFIGURED
        # A live run mutates rows. A dry run reaches here having written nothing,
        # so the commit is a no-op rather than a special case.
        await session.commit()

    rendered = _render_report(report)
    if args.out:
        args.out.write_text(rendered, encoding="utf-8")
        print(f"{report.summary_line()}. Written to {args.out}")
    else:
        print(rendered)

    if args.html_out:
        html_parts = [
            o.rendered_html for o in report.outcomes if o.rendered_html is not None
        ]
        # Concatenated into one file with a rule between them. Every mail client
        # would reject this as a message; it is for eyeballing the layout, and a
        # file per subscription would be worse to review.
        args.html_out.write_text(
            "\n<hr>\n".join(html_parts) or "<p>No digests rendered.</p>", encoding="utf-8"
        )
        print(f"HTML parts written to {args.html_out}")

    return EXIT_PARTIAL_FAILURE if report.failed else EXIT_OK


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
