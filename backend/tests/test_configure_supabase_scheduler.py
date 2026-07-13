from __future__ import annotations

from pathlib import Path

from scripts.configure_supabase_scheduler import JOBS, SECRET_NAME, dispatch_command


ROOT = Path(__file__).resolve().parents[2]


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
