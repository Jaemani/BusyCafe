from __future__ import annotations

from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPOSITORY_ROOT / ".github" / "workflows" / "refresh-cafe-catalog.yml"


def _workflow() -> str:
    return WORKFLOW_PATH.read_text(encoding="utf-8")


def _step(workflow: str, name: str) -> str:
    name_index = workflow.index(f"name: {name}")
    start = workflow.rfind("\n      - ", 0, name_index)
    if start == -1:
        raise ValueError(f"step start not found: {name}")
    start += 1
    next_step = workflow.find("\n      - ", name_index)
    return workflow[start:] if next_step == -1 else workflow[start:next_step]


def test_catalog_refresh_is_manual_only_and_serialized() -> None:
    workflow = _workflow()
    trigger_block = workflow[workflow.index("on:\n") : workflow.index("\npermissions:")]

    assert "workflow_dispatch:" in trigger_block
    assert "schedule:" not in trigger_block
    assert "push:" not in trigger_block
    assert "default: false" in trigger_block
    assert "type: boolean" in trigger_block
    assert "group: production-cafe-catalog-refresh" in workflow
    assert "cancel-in-progress: false" in workflow


def test_catalog_refresh_uses_production_secrets_without_literal_values() -> None:
    workflow = _workflow()

    assert "environment: Production" in workflow
    assert "DATABASE_URL: ${{ secrets.DATABASE_URL }}" in workflow
    assert "SEOUL_API_KEY: ${{ secrets.SEOUL_API_KEY }}" in workflow
    assert "echo \"$DATABASE_URL\"" not in workflow
    assert "echo \"$SEOUL_API_KEY\"" not in workflow


def test_catalog_refresh_defaults_to_dry_run_and_requires_explicit_apply() -> None:
    workflow = _workflow()
    dry_run = _step(workflow, "Dry-run curated cafe seed")
    apply = _step(workflow, "Apply curated cafe seed")

    assert "seed_curated_cafes.py" in dry_run
    assert "--apply" not in dry_run
    assert "if:" not in dry_run
    assert "if: ${{ inputs.apply == true }}" in apply
    assert "seed_curated_cafes.py" in apply
    assert "--apply" in apply


def test_catalog_refresh_materializes_scores_only_after_explicit_apply() -> None:
    workflow = _workflow()
    materialize = _step(workflow, "Materialize cafe scores")

    assert "if: ${{ inputs.apply == true }}" in materialize
    assert "scripts/materialize_scores.py" in materialize
    assert workflow.index("name: Apply curated cafe seed") < workflow.index(
        "name: Materialize cafe scores"
    )


def test_catalog_refresh_uploads_only_aggregate_manifests() -> None:
    upload = _step(_workflow(), "Upload aggregate refresh manifests")

    assert "actions/upload-artifact@v4" in upload
    assert "backend/${{ env.PERMIT_MANIFEST }}" in upload
    assert "backend/${{ env.CURATED_MANIFEST }}" in upload
    assert "OVERTURE_CACHE" not in upload
    assert "PERMIT_CACHE" not in upload
    assert "CURATED_CACHE" not in upload
