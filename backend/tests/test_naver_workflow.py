from __future__ import annotations

import os
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/probe-naver-production.yml"


def _validation_script() -> str:
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    step = next(
        index
        for index, line in enumerate(lines)
        if line.strip() == "name: Check secrets and dry-run bound"
    )
    run = next(
        index
        for index in range(step + 1, len(lines))
        if lines[index].strip() == "run: |"
    )
    indent = len(lines[run]) - len(lines[run].lstrip())
    script: list[str] = []
    for line in lines[run + 1 :]:
        if line and len(line) - len(line.lstrip()) <= indent:
            break
        script.append(line[indent + 2 :] if line else "")
    return "\n".join(script)


def test_workflow_is_manual_production_probe_and_dry_run_only() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    trigger = workflow[workflow.index("on:\n") : workflow.index("\npermissions:")]

    assert "workflow_dispatch:" in trigger
    assert "schedule:" not in trigger
    assert "environment: Production" in workflow
    assert "DATABASE_URL: ${{ secrets.DATABASE_URL }}" in workflow
    assert "NAVER_CLIENT_ID: ${{ secrets.NAVER_CLIENT_ID }}" in workflow
    assert "NAVER_CLIENT_SECRET: ${{ secrets.NAVER_CLIENT_SECRET }}" in workflow
    assert "scripts/probe_naver_local.py" in workflow
    assert (
        'scripts/seed_naver_place_links.py --max-cafes "$MAX_CAFES"'
        in workflow
    )
    assert "--apply" not in workflow
    assert "latitude" not in workflow.lower()
    assert "longitude" not in workflow.lower()


@pytest.mark.parametrize("max_cafes", ["1", "20", "100"])
def test_workflow_validation_accepts_only_small_bounded_runs(max_cafes: str) -> None:
    process = subprocess.run(
        ["bash", "-eu", "-o", "pipefail", "-c", _validation_script()],
        cwd=ROOT,
        env={
            **os.environ,
            "DATABASE_URL": "postgresql://example",
            "NAVER_CLIENT_ID": "example-id",
            "NAVER_CLIENT_SECRET": "example-secret",
            "MAX_CAFES": max_cafes,
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert process.returncode == 0, process.stderr


@pytest.mark.parametrize("max_cafes", ["", "0", "101", "1.5", "abc", "1;true"])
def test_workflow_validation_rejects_unbounded_or_non_integer_runs(
    max_cafes: str,
) -> None:
    process = subprocess.run(
        ["bash", "-eu", "-o", "pipefail", "-c", _validation_script()],
        cwd=ROOT,
        env={
            **os.environ,
            "DATABASE_URL": "postgresql://example",
            "NAVER_CLIENT_ID": "example-id",
            "NAVER_CLIENT_SECRET": "example-secret",
            "MAX_CAFES": max_cafes,
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert process.returncode != 0
    assert "max_cafes must be an integer from 1 through 100" in process.stderr


@pytest.mark.parametrize("missing", ["DATABASE_URL", "NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET"])
def test_workflow_validation_rejects_missing_secret(missing: str) -> None:
    env = {
        **os.environ,
        "DATABASE_URL": "postgresql://example",
        "NAVER_CLIENT_ID": "example-id",
        "NAVER_CLIENT_SECRET": "example-secret",
        "MAX_CAFES": "20",
    }
    env[missing] = ""

    process = subprocess.run(
        ["bash", "-eu", "-o", "pipefail", "-c", _validation_script()],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert process.returncode != 0
    assert "are required" in process.stderr
