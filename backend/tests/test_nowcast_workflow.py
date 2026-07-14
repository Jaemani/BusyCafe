from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_nowcast_workflow_is_scheduled_read_only_and_uses_production_database() -> None:
    workflow = (
        ROOT / ".github/workflows/evaluate-nowcast-production.yml"
    ).read_text(encoding="utf-8")
    trigger = workflow[workflow.index("on:\n") : workflow.index("\npermissions:")]
    assert "workflow_dispatch:" in trigger
    assert "schedule:" in trigger
    assert 'cron: "23 18 * * *"' in trigger
    assert "DATABASE_URL: ${{ secrets.DATABASE_URL }}" in workflow
    assert "run_nowcast_backtest.py" in workflow
    assert "--apply" not in workflow
    assert "deploy" not in workflow.casefold()
