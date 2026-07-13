from __future__ import annotations

from pathlib import Path

import pytest

from app.config import (
    POLL_INTERVAL_MIN,
    PRODUCTION_MONITOR_DELAY_MIN,
    PRODUCTION_POLL_MINUTE_OFFSET,
)
from scripts.configure_supabase_scheduler import (
    JOBS,
    SECRET_NAME,
    dispatch_command,
    minute_schedule,
)


ROOT = Path(__file__).resolve().parents[2]


def _scheduled_minutes(schedule: str) -> tuple[int, ...]:
    return tuple(int(value) for value in schedule.split()[0].split(","))


def test_production_poll_runs_every_five_minutes_and_monitor_is_offset() -> None:
    assert POLL_INTERVAL_MIN == 5
    assert PRODUCTION_POLL_MINUTE_OFFSET == 2
    assert PRODUCTION_MONITOR_DELAY_MIN == 2
    poll_minutes = _scheduled_minutes(JOBS[0].schedule)
    monitor_minutes = _scheduled_minutes(JOBS[1].schedule)

    assert poll_minutes == tuple(range(2, 60, 5))
    assert monitor_minutes == tuple(range(4, 60, 5))
    assert len(poll_minutes) == len(monitor_minutes) == 12
    assert {
        (minute + PRODUCTION_MONITOR_DELAY_MIN) % 60
        for minute in poll_minutes
    } == set(monitor_minutes)


@pytest.mark.parametrize(
    ("interval", "offset"),
    [(0, 0), (7, 0), (5, -1), (5, 5)],
)
def test_minute_schedule_rejects_invalid_interval_or_offset(
    interval: int,
    offset: int,
) -> None:
    with pytest.raises(ValueError):
        minute_schedule(interval, offset)


def test_dispatch_commands_use_vault_without_embedding_secret() -> None:
    for job in JOBS:
        command = dispatch_command(job.workflow)
        assert f"WHERE name = '{SECRET_NAME}'" in command
        assert "vault.decrypted_secrets" in command
        assert "HAVING count(*) = 1" in command
        assert f"/{job.workflow}/dispatches" in command
        assert "'Content-Type', 'application/json'" in command
        assert "body := jsonb_build_object('ref', 'main')" in command


def test_scheduler_workflow_is_manual_and_dry_run_by_default() -> None:
    workflow = (
        ROOT / ".github/workflows/configure-supabase-scheduler.yml"
    ).read_text(encoding="utf-8")
    trigger = workflow[workflow.index("on:\n") : workflow.index("\npermissions:")]
    assert "workflow_dispatch:" in trigger
    assert "schedule:" not in trigger
    assert "default: false" in trigger
    assert "DATABASE_URL: ${{ secrets.DATABASE_URL }}" in workflow
    assert "--apply" in workflow
    assert "echo \"$DATABASE_URL\"" not in workflow


def test_production_workflows_are_dispatched_only_by_external_scheduler() -> None:
    for workflow_name in ("poll-production.yml", "monitor-production.yml"):
        workflow = (ROOT / ".github/workflows" / workflow_name).read_text(
            encoding="utf-8"
        )
        trigger = workflow[workflow.index("on:\n") : workflow.index("\npermissions:")]
        assert "workflow_dispatch:" in trigger
        assert "schedule:" not in trigger
