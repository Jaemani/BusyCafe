from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "apply-kakao-catalog-production.yml"


def _workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_kakao_catalog_apply_is_manual_only_hard_confirmed_and_serialized() -> None:
    workflow = _workflow()
    trigger = workflow[workflow.index("on:\n") : workflow.index("\npermissions:")]

    assert "workflow_dispatch:" in trigger
    assert "schedule:" not in trigger
    assert "push:" not in trigger
    assert "APPLY_KAKAO_CATALOG" in workflow
    assert 'if [ "$CONFIRMATION" != "APPLY_KAKAO_CATALOG" ]' in workflow
    assert "group: production-cafe-catalog-refresh" in workflow
    assert "cancel-in-progress: false" in workflow


def test_kakao_catalog_apply_has_bound_schema_gate_and_rereads_before_apply() -> None:
    workflow = _workflow()
    dry = workflow.index("name: Re-read production and dry-run exact expansion")
    apply = workflow.index("name: Re-read production and apply exact expansion")
    materialize = workflow.index("name: Materialize cafe scores after insert")

    assert 'MAX_EXPECTED_CANDIDATES: ${{ inputs.max_expected_candidates }}' in workflow
    assert "-gt 40000" in workflow
    assert "alembic -c alembic.ini current --check-heads" in workflow
    assert workflow.count("scripts/seed_kakao_catalog_expansion.py") == 2
    assert workflow.count('--max-candidates "$MAX_EXPECTED_CANDIDATES"') == 2
    assert dry < apply < materialize
    assert "--apply" not in workflow[dry:apply]
    assert "--apply" in workflow[apply:materialize]
    assert "scripts/materialize_scores.py" in workflow[materialize:]
