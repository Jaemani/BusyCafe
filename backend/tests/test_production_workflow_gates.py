from __future__ import annotations

import os
from pathlib import Path
import subprocess

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _preflight_script(workflow_name: str, step_name: str) -> str:
    lines = (REPOSITORY_ROOT / ".github" / "workflows" / workflow_name).read_text(
        encoding="utf-8"
    ).splitlines()
    step_index = next(
        index for index, line in enumerate(lines) if line.strip() == f"name: {step_name}"
    )
    run_index = next(
        index
        for index in range(step_index + 1, len(lines))
        if lines[index].strip() == "run: |"
    )
    run_indent = len(lines[run_index]) - len(lines[run_index].lstrip())
    script_lines: list[str] = []
    for line in lines[run_index + 1 :]:
        if line and len(line) - len(line.lstrip()) <= run_indent:
            break
        script_lines.append(line[run_indent + 2 :] if line else "")
    return "\n".join(script_lines)


def _run_preflight(
    tmp_path: Path,
    *,
    workflow_name: str,
    step_name: str,
    env: dict[str, str],
) -> tuple[subprocess.CompletedProcess[str], str]:
    output_path = tmp_path / "github-output"
    process = subprocess.run(
        ["bash", "-eu", "-o", "pipefail", "-c", _preflight_script(workflow_name, step_name)],
        cwd=REPOSITORY_ROOT,
        env={**os.environ, **env, "GITHUB_OUTPUT": str(output_path)},
        capture_output=True,
        text=True,
        check=False,
    )
    output = output_path.read_text(encoding="utf-8") if output_path.exists() else ""
    return process, output


@pytest.mark.parametrize(
    ("workflow_name", "step_name", "dedicated_name", "required_env"),
    [
        (
            "poll-production.yml",
            "Check production secrets",
            "PRODUCTION_POLL_ENABLED",
            {"DATABASE_URL": "postgresql://example", "SEOUL_API_KEY": "example", "PROBE_ONLY": "false"},
        ),
        (
            "monitor-production.yml",
            "Check monitor configuration",
            "PRODUCTION_MONITOR_ENABLED",
            {"PRODUCTION_HEALTH_URL": "https://example.test/api/health"},
        ),
    ],
)
@pytest.mark.parametrize(
    ("dedicated_value", "legacy_value", "expected_enabled"),
    [
        ("true", "false", True),
        ("false", "true", False),
        ("", "true", True),
        ("", "", False),
    ],
)
def test_dedicated_production_gate_is_authoritative_with_legacy_fallback(
    tmp_path: Path,
    workflow_name: str,
    step_name: str,
    dedicated_name: str,
    required_env: dict[str, str],
    dedicated_value: str,
    legacy_value: str,
    expected_enabled: bool,
) -> None:
    process, output = _run_preflight(
        tmp_path,
        workflow_name=workflow_name,
        step_name=step_name,
        env={
            **required_env,
            dedicated_name: dedicated_value,
            "PRODUCTION_ENABLED": legacy_value,
        },
    )

    assert process.returncode == 0, process.stderr
    assert f"enabled={'true' if expected_enabled else 'false'}" in output


@pytest.mark.parametrize(
    ("workflow_name", "step_name", "dedicated_name", "required_env"),
    [
        (
            "poll-production.yml",
            "Check production secrets",
            "PRODUCTION_POLL_ENABLED",
            {"DATABASE_URL": "postgresql://example", "SEOUL_API_KEY": "example", "PROBE_ONLY": "false"},
        ),
        (
            "monitor-production.yml",
            "Check monitor configuration",
            "PRODUCTION_MONITOR_ENABLED",
            {"PRODUCTION_HEALTH_URL": "https://example.test/api/health"},
        ),
    ],
)
@pytest.mark.parametrize(
    ("dedicated_value", "legacy_value"),
    [("yes", "true"), ("", "yes")],
)
def test_production_gate_rejects_ambiguous_values(
    tmp_path: Path,
    workflow_name: str,
    step_name: str,
    dedicated_name: str,
    required_env: dict[str, str],
    dedicated_value: str,
    legacy_value: str,
) -> None:
    process, _ = _run_preflight(
        tmp_path,
        workflow_name=workflow_name,
        step_name=step_name,
        env={
            **required_env,
            dedicated_name: dedicated_value,
            "PRODUCTION_ENABLED": legacy_value,
        },
    )

    assert process.returncode != 0
    assert "must be exactly true or false" in process.stderr


@pytest.mark.parametrize(
    ("workflow_name", "step_name", "dedicated_name", "required_env"),
    [
        (
            "poll-production.yml",
            "Check production secrets",
            "PRODUCTION_POLL_ENABLED",
            {"DATABASE_URL": "", "SEOUL_API_KEY": "", "PROBE_ONLY": "false"},
        ),
        (
            "monitor-production.yml",
            "Check monitor configuration",
            "PRODUCTION_MONITOR_ENABLED",
            {"PRODUCTION_HEALTH_URL": ""},
        ),
    ],
)
def test_enabled_production_gate_fails_when_required_configuration_is_missing(
    tmp_path: Path,
    workflow_name: str,
    step_name: str,
    dedicated_name: str,
    required_env: dict[str, str],
) -> None:
    process, _ = _run_preflight(
        tmp_path,
        workflow_name=workflow_name,
        step_name=step_name,
        env={
            **required_env,
            dedicated_name: "true",
            "PRODUCTION_ENABLED": "false",
        },
    )

    assert process.returncode != 0
    assert "is enabled but" in process.stderr
