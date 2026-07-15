from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "apply-kakao-catalog-production.yml"


def _workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_kakao_catalog_apply_is_weekly_manual_capable_and_serialized() -> None:
    workflow = _workflow()
    trigger = workflow[workflow.index("on:\n") : workflow.index("\npermissions:")]

    assert "workflow_dispatch:" in trigger
    assert "schedule:" in trigger
    assert 'cron: "23 18 * * 1"' in trigger
    assert "push:" not in trigger
    assert "APPLY_KAKAO_CATALOG" in workflow
    assert 'if [ "$CONFIRMATION" != "APPLY_KAKAO_CATALOG" ]' in workflow
    assert "group: production-cafe-catalog-refresh" in workflow
    assert "cancel-in-progress: false" in workflow


def test_scheduled_kakao_apply_uses_immutable_fail_closed_bounds() -> None:
    workflow = _workflow()

    assert (
        "MAX_EXPECTED_CANDIDATES: "
        "${{ github.event_name == 'schedule' && '2000' "
        "|| inputs.max_expected_candidates }}"
    ) in workflow
    assert (
        "MAX_EXPECTED_LARGE_MOVES: "
        "${{ github.event_name == 'schedule' && '0' "
        "|| inputs.max_expected_large_moves }}"
    ) in workflow
    assert 'if [ "$GITHUB_EVENT_NAME" = "schedule" ]; then' in workflow
    assert 'if [ "$MAX_EXPECTED_CANDIDATES" != "2000" ]' in workflow
    assert '[ "$MAX_EXPECTED_LARGE_MOVES" != "0" ]' in workflow


def test_kakao_catalog_apply_has_bound_schema_gate_and_rereads_before_apply() -> None:
    workflow = _workflow()
    sweep = workflow.index("name: Sweep and validate current Kakao CE7 catalog")
    schema = workflow.index("name: Verify production database schema")
    dry = workflow.index("name: Re-read production and dry-run exact expansion")
    apply = workflow.index("name: Re-read production and apply exact expansion")
    materialize = workflow.index("name: Materialize cafe scores after insert")

    assert "inputs.max_expected_candidates" in workflow
    assert "-gt 40000" in workflow
    assert "alembic -c alembic.ini current --check-heads" in workflow
    assert workflow.count("scripts/seed_kakao_catalog_expansion.py") == 2
    assert workflow.count('--max-candidates "$MAX_EXPECTED_CANDIDATES"') == 2
    assert workflow.count('--max-large-moves "$MAX_EXPECTED_LARGE_MOVES"') == 2
    assert sweep < schema < dry < apply < materialize
    assert "--apply" not in workflow[dry:apply]
    assert "--apply" in workflow[apply:materialize]
    assert "scripts/materialize_scores.py" in workflow[materialize:]


def test_kakao_catalog_apply_preserves_manifest_and_bounded_run_reports() -> None:
    workflow = _workflow()
    upload = workflow[workflow.index("name: Upload validated Kakao manifest") :]

    assert workflow.count("set -o pipefail") == 2
    assert '| tee "$KAKAO_DRY_RUN_REPORT"' in workflow
    assert '| tee "$KAKAO_APPLY_REPORT"' in workflow
    assert "backend/${{ env.KAKAO_MANIFEST }}" in upload
    assert "backend/${{ env.KAKAO_DRY_RUN_REPORT }}" in upload
    assert "backend/${{ env.KAKAO_APPLY_REPORT }}" in upload
    assert "KAKAO_CACHE" not in upload
    assert "retention-days: 30" in upload
