from pathlib import Path

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = (
    REPOSITORY_ROOT
    / ".github"
    / "workflows"
    / "select-eval-candidates-production.yml"
)


def test_eval_candidate_workflow_is_read_only_and_uploads_csv() -> None:
    raw = WORKFLOW.read_text(encoding="utf-8")
    workflow = yaml.safe_load(raw)
    steps = workflow["jobs"]["select"]["steps"]
    commands = "\n".join(
        str(step.get("run", "")) for step in steps if isinstance(step, dict)
    )

    assert "workflow_dispatch:" in raw
    assert "select_eval_candidates.py" in commands
    assert "--output" in commands
    assert "--require-complete" in commands
    assert "--apply" not in commands
    assert "upgrade head" not in commands
    assert any(
        step.get("uses") == "actions/upload-artifact@v6"
        for step in steps
        if isinstance(step, dict)
    )
