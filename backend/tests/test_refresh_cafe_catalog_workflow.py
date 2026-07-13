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
    assert "KAKAO_REST_KEY: ${{ secrets.KAKAO_REST_KEY }}" in workflow
    assert "echo \"$DATABASE_URL\"" not in workflow
    assert "echo \"$SEOUL_API_KEY\"" not in workflow
    assert "echo \"$KAKAO_REST_KEY\"" not in workflow


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
    assert workflow.index("name: Apply provider cafe seed") < workflow.index(
        "name: Materialize cafe scores"
    )


def test_catalog_refresh_builds_kakao_provider_stage_before_database_seed() -> None:
    workflow = _workflow()
    kakao = _step(workflow, "Sweep Kakao CE7 cafe catalog")
    build = _step(workflow, "Build provider cafe catalog")
    dry_run = _step(workflow, "Dry-run provider cafe seed")
    apply = _step(workflow, "Apply provider cafe seed")

    assert "cache_kakao_cafes.py" in kakao
    assert "--apply" in kakao
    assert "build_provider_cafe_catalog.py" in build
    assert '--kakao-manifest "$KAKAO_MANIFEST"' in build
    assert "--curated-cache" in build
    assert "--permit-cache" in build
    assert "--kakao-cache" in build
    assert "seed_provider_cafes.py" in dry_run
    assert "--apply" not in dry_run
    assert "if:" not in dry_run
    assert "if: ${{ inputs.apply == true }}" in apply
    assert "seed_provider_cafes.py" in apply
    assert "--apply" in apply
    assert workflow.index("name: Build provider cafe catalog") < workflow.index(
        "name: Dry-run provider cafe seed"
    )


def test_catalog_refresh_changes_schema_only_with_explicit_apply() -> None:
    upgrade = _step(_workflow(), "Upgrade production database schema")

    assert "if: ${{ inputs.apply == true }}" in upgrade
    assert "alembic -c alembic.ini upgrade head" in upgrade


def test_catalog_refresh_uploads_only_aggregate_manifests() -> None:
    upload = _step(_workflow(), "Upload aggregate refresh manifests")

    assert "actions/upload-artifact@v6" in upload
    assert "backend/${{ env.PERMIT_MANIFEST }}" in upload
    assert "backend/${{ env.CURATED_MANIFEST }}" in upload
    assert "backend/${{ env.KAKAO_MANIFEST }}" in upload
    assert "backend/${{ env.PROVIDER_MANIFEST }}" in upload
    assert "OVERTURE_CACHE" not in upload
    assert "PERMIT_CACHE" not in upload
    assert "CURATED_CACHE" not in upload
    assert "KAKAO_CACHE" not in upload
    assert "PROVIDER_CACHE" not in upload
