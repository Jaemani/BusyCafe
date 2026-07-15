from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_ingest_slo_workflow_is_weekly_read_only_and_production_scoped() -> None:
    workflow = (
        ROOT / ".github/workflows/analyze-ingest-slo-production.yml"
    ).read_text(encoding="utf-8")
    trigger = workflow[workflow.index("on:\n") : workflow.index("\npermissions:")]

    assert "workflow_dispatch:" in trigger
    assert "schedule:" in trigger
    assert 'cron: "37 19 * * 2"' in trigger
    assert 'default: "24"' in trigger
    assert "environment: Production" in workflow
    assert "DATABASE_URL: ${{ secrets.DATABASE_URL }}" in workflow
    assert (
        "WINDOW_HOURS: ${{ github.event_name == 'workflow_dispatch' "
        "&& inputs.window_hours || '168' }}"
    ) in workflow
    assert "analyze_ingest_slo.py" in workflow
    assert '--window-hours "$WINDOW_HOURS"' in workflow
    assert "--apply" not in workflow
