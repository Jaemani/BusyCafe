"""Configure Supabase cron jobs that dispatch production GitHub workflows."""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass

import psycopg


SECRET_NAME = "busy_cafe_github_pat"
REPOSITORY = "Jaemani/BusyCafe"


@dataclass(frozen=True, slots=True)
class Job:
    name: str
    schedule: str
    workflow: str


JOBS = (
    Job(
        "busy-cafe-poll-production",
        "7,17,27,37,47,57 * * * *",
        "poll-production.yml",
    ),
    Job(
        "busy-cafe-monitor-production",
        "9,19,29,39,49,59 * * * *",
        "monitor-production.yml",
    ),
)


def dispatch_command(workflow: str) -> str:
    """Build command stored by pg_cron without embedding decrypted secret."""

    url = (
        f"https://api.github.com/repos/{REPOSITORY}/actions/workflows/"
        f"{workflow}/dispatches"
    )
    return f"""SELECT net.http_post(
  url := '{url}',
  headers := jsonb_build_object(
    'Authorization', 'Bearer ' || secret.value,
    'Accept', 'application/vnd.github+json',
    'Content-Type', 'application/json',
    'X-GitHub-Api-Version', '2022-11-28',
    'User-Agent', 'BusyCafe-Supabase-Scheduler'
  ),
  body := jsonb_build_object('ref', 'main')
)
FROM (
  SELECT max(decrypted_secret) AS value
  FROM vault.decrypted_secrets
  WHERE name = '{SECRET_NAME}'
  HAVING count(*) = 1
) AS secret;"""


def _extension_names(connection: psycopg.Connection[object]) -> set[str]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT extname FROM pg_extension WHERE extname = ANY(%s)",
            (["pg_cron", "pg_net"],),
        )
        return {str(row[0]) for row in cursor.fetchall()}


def _secret_count(connection: psycopg.Connection[object]) -> int:
    with connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass('vault.secrets') IS NOT NULL")
        if not cursor.fetchone()[0]:
            return 0
        cursor.execute(
            "SELECT count(*) FROM vault.secrets WHERE name = %s",
            (SECRET_NAME,),
        )
        return int(cursor.fetchone()[0])


def _jobs(connection: psycopg.Connection[object]) -> dict[str, tuple[str, str, bool]]:
    if "pg_cron" not in _extension_names(connection):
        return {}
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT jobname, schedule, command, active FROM cron.job "
            "WHERE jobname = ANY(%s)",
            ([job.name for job in JOBS],),
        )
        return {
            str(name): (str(schedule), str(command), bool(active))
            for name, schedule, command, active in cursor.fetchall()
        }


def report(connection: psycopg.Connection[object]) -> bool:
    extensions = _extension_names(connection)
    secret_count = _secret_count(connection)
    existing = _jobs(connection)
    print(
        "extensions: "
        f"pg_cron={'present' if 'pg_cron' in extensions else 'missing'} "
        f"pg_net={'present' if 'pg_net' in extensions else 'missing'}"
    )
    print(f"vault secret: name={SECRET_NAME} count={secret_count}")
    all_match = secret_count == 1 and extensions == {"pg_cron", "pg_net"}
    for job in JOBS:
        row = existing.get(job.name)
        matched = row == (job.schedule, dispatch_command(job.workflow), True)
        status = "matched" if matched else ("missing" if row is None else "drift")
        print(f"cron job: name={job.name} status={status}")
        all_match = all_match and matched
    return all_match


def apply(connection: psycopg.Connection[object]) -> None:
    if _secret_count(connection) != 1:
        raise RuntimeError(f"vault secret {SECRET_NAME} must exist exactly once")
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute("CREATE EXTENSION IF NOT EXISTS pg_net")
        cursor.execute("CREATE EXTENSION IF NOT EXISTS pg_cron")
        for job in JOBS:
            cursor.execute(
                "SELECT cron.schedule(%s, %s, %s)",
                (job.name, job.schedule, dispatch_command(job.workflow)),
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is required", file=sys.stderr)
        return 1

    try:
        with psycopg.connect(database_url) as connection:
            print(f"mode={'apply' if args.apply else 'dry-run'}")
            if args.apply:
                apply(connection)
            matched = report(connection)
    except (psycopg.Error, RuntimeError) as exc:
        print(f"scheduler configuration failed: {type(exc).__name__}", file=sys.stderr)
        return 1

    if args.apply and not matched:
        print("scheduler verification failed", file=sys.stderr)
        return 1
    if not args.apply:
        print("dry-run: no changes applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
