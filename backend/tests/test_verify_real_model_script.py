from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
VERIFY_REAL_MODEL_PATH = ROOT / "scripts" / "verify_real_model.ps1"
VERIFY_LOCAL_PATH = ROOT / "scripts" / "verify_local.ps1"
EXPECTED_RUNNER_ENVIRONMENT = {
    "LOCAL_MODEL_BASE_URL": "http://localhost:11434",
    "LOCAL_MODEL_NAME": "qwen3.5:9b",
    "LOCAL_MODEL_TIMEOUT_SECONDS": "120",
    "LOCAL_MODEL_CONTEXT_LENGTH": "16384",
    "LOCAL_MODEL_TEMPERATURE": "0",
    "LOCAL_MODEL_SEED": "42",
    "LOCAL_MODEL_MAX_OUTPUT_TOKENS": "2048",
    "LOCAL_MODEL_THINK": "true",
}


def _powershell() -> str:
    executable = shutil.which("powershell") or shutil.which("pwsh")
    if executable is None:
        pytest.skip("PowerShell is required for real-model verifier tests")
    return executable


def _ps_quote(value: Path | str) -> str:
    return str(value).replace("'", "''")


def _valid_report() -> dict[str, object]:
    return {
        "evaluation_kind": "live_local_model_synthetic_cases",
        "generated_at": "2026-08-10T00:00:00Z",
        "model_artifact": {
            "tag": "qwen3.5:9b",
            "digest": "sha256:verified-local-artifact",
            "quantization": "Q4_K_M",
        },
        "configuration": {
            "endpoint": "http://host.docker.internal:11434",
            "model_name": "qwen3.5:9b",
            "context_length": 16384,
            "temperature": 0.0,
            "seed": 42,
            "max_output_tokens": 2048,
            "reasoning_enabled": True,
            "timeout_seconds": 120.0,
        },
        "case_count": 3,
        "repeat_count": 3,
        "raw_model": {
            "attempt_count": 18,
            "response_model_match_rate": {
                "numerator": 18,
                "denominator": 18,
                "rate": 1.0,
            },
            "raw_response": "SECRET_MODEL_PROSE_MUST_NOT_LEAK",
        },
        "validated_system": {
            "job_count": 9,
            "terminal_distribution": {
                "actionable": 3,
                "needs_evidence": 3,
                "refused": 2,
                "failed": 1,
            },
            "effective_output_rate": {
                "numerator": 6,
                "denominator": 9,
                "rate": 0.667,
            },
            "expected_status_match_rate": {
                "numerator": 8,
                "denominator": 9,
                "rate": 0.889,
            },
            "persisted_unknown_citation_count": 0,
            "unsupported_numeric_claim_count": 0,
            "redacted_reference_count": 0,
        },
        "runtime": {"provider_attempt_count": 18},
        "prompt": "SECRET_PROMPT_MUST_NOT_LEAK",
    }


def _write_fake_python(tmp_path: Path) -> Path:
    fake_python = tmp_path / "fake-python.ps1"
    fake_python.write_text(
        """param(
  [string]$RunnerPath,
  [string]$CasesFlag,
  [string]$CasesPath,
  [string]$OutputJsonFlag,
  [string]$OutputJsonPath,
  [string]$OutputMarkdownFlag,
  [string]$OutputMarkdownPath,
  [string]$RepeatsFlag,
  [string]$RepeatsValue
)
$utf8 = [System.Text.UTF8Encoding]::new($false)
$audit = [ordered]@{
  runner_path = $RunnerPath
  cases_flag = $CasesFlag
  cases_path = $CasesPath
  output_json_flag = $OutputJsonFlag
  output_json_path = $OutputJsonPath
  output_markdown_flag = $OutputMarkdownFlag
  output_markdown_path = $OutputMarkdownPath
  repeats_flag = $RepeatsFlag
  repeats_value = $RepeatsValue
  local_model_environment = [ordered]@{
    LOCAL_MODEL_BASE_URL = [System.Environment]::GetEnvironmentVariable(
      'LOCAL_MODEL_BASE_URL', 'Process'
    )
    LOCAL_MODEL_NAME = [System.Environment]::GetEnvironmentVariable(
      'LOCAL_MODEL_NAME', 'Process'
    )
    LOCAL_MODEL_TIMEOUT_SECONDS = [System.Environment]::GetEnvironmentVariable(
      'LOCAL_MODEL_TIMEOUT_SECONDS', 'Process'
    )
    LOCAL_MODEL_CONTEXT_LENGTH = [System.Environment]::GetEnvironmentVariable(
      'LOCAL_MODEL_CONTEXT_LENGTH', 'Process'
    )
    LOCAL_MODEL_TEMPERATURE = [System.Environment]::GetEnvironmentVariable(
      'LOCAL_MODEL_TEMPERATURE', 'Process'
    )
    LOCAL_MODEL_SEED = [System.Environment]::GetEnvironmentVariable(
      'LOCAL_MODEL_SEED', 'Process'
    )
    LOCAL_MODEL_MAX_OUTPUT_TOKENS = [System.Environment]::GetEnvironmentVariable(
      'LOCAL_MODEL_MAX_OUTPUT_TOKENS', 'Process'
    )
    LOCAL_MODEL_THINK = [System.Environment]::GetEnvironmentVariable(
      'LOCAL_MODEL_THINK', 'Process'
    )
  }
  working_directory = (Get-Location).Path
}
[System.IO.File]::WriteAllText(
  $env:VERIFY_RUNNER_AUDIT_PATH,
  ($audit | ConvertTo-Json -Compress),
  $utf8
)
if ($env:VERIFY_RUNNER_EXIT_CODE -ne '0') {
  throw 'mock runner failure'
}
$payload = [System.IO.File]::ReadAllText(
  $env:VERIFY_RESULT_FIXTURE_PATH,
  $utf8
)
[System.IO.File]::WriteAllText($OutputJsonPath, $payload, $utf8)
[System.IO.File]::WriteAllText($OutputMarkdownPath, "# mock report`n", $utf8)
if ($env:VERIFY_FORCE_STALE -eq '1') {
  (Get-Item -LiteralPath $OutputJsonPath).LastWriteTimeUtc =
    (Get-Item -LiteralPath $RunnerPath).LastWriteTimeUtc.AddSeconds(-1)
}
""",
        encoding="utf-8",
    )
    return fake_python


def _run_verifier(
    tmp_path: Path,
    report: dict[str, object],
    *,
    configured: bool = True,
    ready: bool = True,
    health_model: str = "qwen3.5:9b",
    force_stale: bool = False,
    runner_exit_code: int = 0,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    fixture_path = tmp_path / "fixture-result.json"
    runner_path = tmp_path / "run_local_model_eval.py"
    cases_path = tmp_path / "live_model_cases.jsonl"
    results_path = tmp_path / "local_model-results.json"
    markdown_path = tmp_path / "LOCAL_MODEL_EVAL_REPORT.md"
    audit_path = tmp_path / "runner-audit.json"
    harness_path = tmp_path / "verify-real-model-harness.ps1"
    fake_python = _write_fake_python(tmp_path)

    fixture_path.write_text(json.dumps(report), encoding="utf-8")
    runner_path.write_text("# mocked live runner\n", encoding="utf-8")
    cases_path.write_text("{}\n", encoding="utf-8")
    baseline = time.time() - 120
    os.utime(runner_path, (baseline, baseline))
    os.utime(cases_path, (baseline, baseline))

    configured_ps = "$true" if configured else "$false"
    ready_ps = "$true" if ready else "$false"
    stale_ps = "1" if force_stale else "0"
    harness_path.write_text(
        f"""$ErrorActionPreference = 'Stop'
$OutputEncoding = [Console]::OutputEncoding =
  [System.Text.UTF8Encoding]::new($false)
Set-Location -LiteralPath '{_ps_quote(tmp_path)}'
$startingLocation = (Get-Location).Path
$env:VERIFY_RESULT_FIXTURE_PATH = '{_ps_quote(fixture_path)}'
$env:VERIFY_RUNNER_AUDIT_PATH = '{_ps_quote(audit_path)}'
$env:VERIFY_RUNNER_EXIT_CODE = '{runner_exit_code}'
$env:VERIFY_FORCE_STALE = '{stale_ps}'
$pollutedEnvironment = [ordered]@{{
  LOCAL_MODEL_BASE_URL = 'http://polluted.invalid:9999'
  LOCAL_MODEL_NAME = 'polluted:model'
  LOCAL_MODEL_TIMEOUT_SECONDS = '999'
  LOCAL_MODEL_CONTEXT_LENGTH = '4096'
  LOCAL_MODEL_TEMPERATURE = '1.5'
  LOCAL_MODEL_SEED = '999'
  LOCAL_MODEL_MAX_OUTPUT_TOKENS = '999'
  LOCAL_MODEL_THINK = 'false'
}}
foreach ($entry in $pollutedEnvironment.GetEnumerator()) {{
  [System.Environment]::SetEnvironmentVariable(
    $entry.Key, $entry.Value, 'Process'
  )
}}
$global:auditTimeouts = @()
function Invoke-RestMethod {{
  param([Parameter(Position=0)][string]$Uri, [int]$TimeoutSec)
  $global:auditTimeouts += $TimeoutSec
  return [pscustomobject]@{{
    configured = {configured_ps}
    ready = {ready_ps}
    provider = 'ollama'
    model_name = '{_ps_quote(health_model)}'
    error_code = $null
  }}
}}
$verifierError = $null
try {{
  & '{_ps_quote(VERIFY_REAL_MODEL_PATH)}' `
    -ApiBaseUrl 'http://localhost:8000' `
    -ExpectedModel 'qwen3.5:9b' `
    -Repeats 3 `
    -RequestTimeoutSeconds 17 `
    -PythonExecutable '{_ps_quote(fake_python)}' `
    -RunnerPath '{_ps_quote(runner_path)}' `
    -CasesPath '{_ps_quote(cases_path)}' `
    -ResultsPath '{_ps_quote(results_path)}' `
    -MarkdownReportPath '{_ps_quote(markdown_path)}'
}}
catch {{
  $verifierError = $_
}}
if ((Get-Location).Path -ne $startingLocation) {{
  throw 'The verifier must restore the caller working directory.'
}}
foreach ($entry in $pollutedEnvironment.GetEnumerator()) {{
  $restoredValue = [System.Environment]::GetEnvironmentVariable(
    $entry.Key, 'Process'
  )
  if ($restoredValue -ne $entry.Value) {{
    throw "The verifier must restore $($entry.Key) after the runner."
  }}
}}
if ($global:auditTimeouts.Count -ne 1 -or $global:auditTimeouts[0] -ne 17) {{
  throw 'The mocked health request must receive TimeoutSec=17.'
}}
if ($null -ne $verifierError) {{
  throw $verifierError
}}
""",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            _powershell(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(harness_path),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
    )
    return completed, audit_path


def _combined_output(completed: subprocess.CompletedProcess[str]) -> str:
    return completed.stdout + completed.stderr


def _run_verifier_with_only_public_parameters(
    tmp_path: Path,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    project_root = tmp_path / "standalone-project"
    scripts_dir = project_root / "scripts"
    eval_dir = project_root / "eval"
    docs_dir = project_root / "docs"
    venv_scripts_dir = project_root / ".venv" / "Scripts"
    scripts_dir.mkdir(parents=True)
    eval_dir.mkdir()
    docs_dir.mkdir()
    venv_scripts_dir.mkdir(parents=True)

    verifier_path = scripts_dir / "verify_real_model.ps1"
    runner_path = eval_dir / "run_local_model_eval.py"
    results_path = eval_dir / "local_model-results.json"
    markdown_path = docs_dir / "LOCAL_MODEL_EVAL_REPORT.md"
    verifier_source = VERIFY_REAL_MODEL_PATH.read_text(encoding="utf-8")
    health_mock = """function Invoke-RestMethod {
  param([Parameter(Position=0)][string]$Uri, [int]$TimeoutSec)
  return [pscustomobject]@{
    configured = $true
    ready = $true
    provider = 'ollama'
    model_name = 'qwen3.5:9b'
    error_code = $null
  }
}

"""
    verifier_path.write_text(
        verifier_source.replace(
            "Set-StrictMode -Version Latest",
            health_mock + "Set-StrictMode -Version Latest",
            1,
        ),
        encoding="utf-8",
    )
    shutil.copy2(ROOT / ".venv" / "Scripts" / "python.exe", venv_scripts_dir)
    shutil.copy2(
        ROOT / ".venv" / "pyvenv.cfg",
        project_root / ".venv" / "pyvenv.cfg",
    )
    (eval_dir / "live_model_cases.jsonl").write_text("{}\n", encoding="utf-8")

    serialized_report = json.dumps(_valid_report(), ensure_ascii=False)
    runner_path.write_text(
        """from __future__ import annotations

import argparse
from pathlib import Path


parser = argparse.ArgumentParser()
parser.add_argument("--cases")
parser.add_argument("--output-json")
parser.add_argument("--output-markdown")
parser.add_argument("--repeats")
args = parser.parse_args()
Path(args.output_json).write_text(REPORT, encoding="utf-8")
Path(args.output_markdown).write_text("# mock report\\n", encoding="utf-8")
print("SECRET_FAKE_RUNNER_OUTPUT_MUST_NOT_LEAK")
""".replace("REPORT", repr(serialized_report)),
        encoding="utf-8",
    )
    assert not (project_root / ".env").exists()

    completed = subprocess.run(
        [
            _powershell(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(verifier_path),
            "-ApiBaseUrl",
            "http://localhost:8000",
            "-ExpectedModel",
            "qwen3.5:9b",
            "-Repeats",
            "3",
            "-RequestTimeoutSeconds",
            "17",
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
        cwd=tmp_path,
    )
    assert results_path.parent == eval_dir
    return completed, markdown_path


def test_real_model_verifier_exists() -> None:
    assert VERIFY_REAL_MODEL_PATH.is_file()


def test_verifier_resolves_default_paths_after_parameter_binding(
    tmp_path: Path,
) -> None:
    completed, markdown_path = _run_verifier_with_only_public_parameters(tmp_path)

    assert completed.returncode == 0, _combined_output(completed)
    assert '"status":"passed"' in completed.stdout
    assert "SECRET_FAKE_RUNNER_OUTPUT_MUST_NOT_LEAK" not in completed.stdout
    assert markdown_path.is_file()


@pytest.mark.parametrize(
    ("configured", "ready", "expected_message"),
    [
        (False, False, "configured"),
        (True, False, "ready"),
    ],
)
def test_verifier_requires_configured_and_ready_health(
    tmp_path: Path,
    configured: bool,
    ready: bool,
    expected_message: str,
) -> None:
    completed, _ = _run_verifier(
        tmp_path,
        _valid_report(),
        configured=configured,
        ready=ready,
    )

    assert completed.returncode != 0
    assert expected_message in _combined_output(completed).lower()


def test_verifier_rejects_wrong_health_model(tmp_path: Path) -> None:
    completed, _ = _run_verifier(
        tmp_path,
        _valid_report(),
        health_model="qwen3.5:4b",
    )

    assert completed.returncode != 0
    assert "model" in _combined_output(completed).lower()


def test_verifier_rejects_failed_runner(tmp_path: Path) -> None:
    completed, _ = _run_verifier(
        tmp_path,
        _valid_report(),
        runner_exit_code=7,
    )

    assert completed.returncode != 0
    assert "runner" in _combined_output(completed).lower()


def test_verifier_rejects_stale_output(tmp_path: Path) -> None:
    completed, _ = _run_verifier(
        tmp_path,
        _valid_report(),
        force_stale=True,
    )

    assert completed.returncode != 0
    assert "stale" in _combined_output(completed).lower()


def test_verifier_requires_nonempty_digest(tmp_path: Path) -> None:
    report = _valid_report()
    report["model_artifact"]["digest"] = "  "  # type: ignore[index]

    completed, _ = _run_verifier(tmp_path, report)

    assert completed.returncode != 0
    assert "digest" in _combined_output(completed).lower()


def test_verifier_requires_configuration_section(tmp_path: Path) -> None:
    report = _valid_report()
    del report["configuration"]

    completed, _ = _run_verifier(tmp_path, report)

    assert completed.returncode != 0
    assert "configuration" in _combined_output(completed).lower()


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("model_name", "qwen3.5:4b"),
        ("context_length", 8192),
        ("context_length", "16384"),
        ("context_length", True),
        ("temperature", 0.1),
        ("temperature", "0"),
        ("temperature", False),
        ("seed", 7),
        ("seed", "42"),
        ("seed", True),
        ("max_output_tokens", 1024),
        ("max_output_tokens", "2048"),
        ("max_output_tokens", True),
        ("reasoning_enabled", False),
        ("reasoning_enabled", "true"),
        ("timeout_seconds", 121.0),
        ("timeout_seconds", "120"),
        ("timeout_seconds", True),
    ],
)
def test_verifier_requires_exact_typed_configuration(
    tmp_path: Path,
    field: str,
    invalid_value: object,
) -> None:
    report = _valid_report()
    report["configuration"][field] = invalid_value  # type: ignore[index]

    completed, _ = _run_verifier(tmp_path, report)

    assert completed.returncode != 0
    assert field in _combined_output(completed)


@pytest.mark.parametrize("section", ["raw_model", "validated_system", "runtime"])
def test_verifier_requires_all_metric_layers(tmp_path: Path, section: str) -> None:
    report = _valid_report()
    del report[section]

    completed, _ = _run_verifier(tmp_path, report)

    assert completed.returncode != 0
    assert section in _combined_output(completed)


def test_verifier_requires_response_model_match_rate(tmp_path: Path) -> None:
    report = _valid_report()
    del report["raw_model"]["response_model_match_rate"]  # type: ignore[index]

    completed, _ = _run_verifier(tmp_path, report)

    assert completed.returncode != 0
    assert "response_model_match_rate" in _combined_output(completed)
    assert '"status":"passed"' not in completed.stdout


@pytest.mark.parametrize(
    "invalid_ratio",
    [
        None,
        "1.0",
        True,
        [],
    ],
)
def test_verifier_rejects_malformed_response_model_match_rate(
    tmp_path: Path,
    invalid_ratio: object,
) -> None:
    report = _valid_report()
    report["raw_model"]["response_model_match_rate"] = invalid_ratio  # type: ignore[index]

    completed, _ = _run_verifier(tmp_path, report)

    assert completed.returncode != 0
    assert "response_model_match_rate" in _combined_output(completed)
    assert '"status":"passed"' not in completed.stdout


@pytest.mark.parametrize(
    ("invalid_ratio", "expected_message"),
    [
        ({"denominator": 1, "rate": 1.0}, "numerator"),
        ({"numerator": 1, "rate": 1.0}, "denominator"),
        ({"numerator": 1, "denominator": 1}, "rate"),
        ({"numerator": "1", "denominator": 1, "rate": 1.0}, "numerator"),
        ({"numerator": True, "denominator": 1, "rate": 1.0}, "numerator"),
        ({"numerator": None, "denominator": 1, "rate": 1.0}, "numerator"),
        ({"numerator": 1, "denominator": "1", "rate": 1.0}, "denominator"),
        ({"numerator": 1, "denominator": True, "rate": 1.0}, "denominator"),
        ({"numerator": 1, "denominator": None, "rate": 1.0}, "denominator"),
        ({"numerator": 0, "denominator": 0, "rate": None}, "denominator"),
        ({"numerator": 0, "denominator": 1, "rate": 0.0}, "numerator"),
        ({"numerator": 1, "denominator": 2, "rate": 0.5}, "numerator"),
        ({"numerator": 1, "denominator": 1, "rate": "1.0"}, "rate"),
        ({"numerator": 1, "denominator": 1, "rate": True}, "rate"),
        ({"numerator": 1, "denominator": 1, "rate": None}, "rate"),
        ({"numerator": 1, "denominator": 1, "rate": 0.9999999999}, "rate"),
    ],
)
def test_verifier_rejects_invalid_response_model_match_ratio(
    tmp_path: Path,
    invalid_ratio: dict[str, object],
    expected_message: str,
) -> None:
    report = _valid_report()
    report["raw_model"]["response_model_match_rate"] = invalid_ratio  # type: ignore[index]

    completed, _ = _run_verifier(tmp_path, report)

    assert completed.returncode != 0
    assert expected_message in _combined_output(completed).lower()
    assert '"status":"passed"' not in completed.stdout


@pytest.mark.parametrize(
    "metric",
    [
        "persisted_unknown_citation_count",
        "unsupported_numeric_claim_count",
        "redacted_reference_count",
    ],
)
def test_verifier_requires_zero_persisted_safety_counts(
    tmp_path: Path,
    metric: str,
) -> None:
    report = _valid_report()
    report["validated_system"][metric] = 1  # type: ignore[index]

    completed, _ = _run_verifier(tmp_path, report)

    assert completed.returncode != 0
    assert metric in _combined_output(completed)


def test_verifier_requires_expected_status_match_rate(tmp_path: Path) -> None:
    report = _valid_report()
    del report["validated_system"][  # type: ignore[index]
        "expected_status_match_rate"
    ]

    completed, _ = _run_verifier(tmp_path, report)

    assert completed.returncode != 0
    assert "expected_status_match_rate" in _combined_output(completed)


@pytest.mark.parametrize(
    ("invalid_ratio", "expected_message"),
    [
        (
            {"numerator": 7, "denominator": 8, "rate": 0.875},
            "denominator",
        ),
        (
            {"numerator": 10, "denominator": 9, "rate": 1.111},
            "numerator",
        ),
        (
            {"numerator": 8, "denominator": 9, "rate": 0.888},
            "rate",
        ),
        (
            {"numerator": 8, "denominator": 9, "rate": "0.889"},
            "rate",
        ),
        (
            {"numerator": 8, "denominator": 9, "rate": True},
            "rate",
        ),
        (
            {"numerator": 8, "denominator": "9", "rate": 0.889},
            "denominator",
        ),
        (
            {"numerator": True, "denominator": 9, "rate": 0.889},
            "numerator",
        ),
    ],
)
def test_verifier_rejects_invalid_expected_status_match_ratio(
    tmp_path: Path,
    invalid_ratio: dict[str, object],
    expected_message: str,
) -> None:
    report = _valid_report()
    report["validated_system"][  # type: ignore[index]
        "expected_status_match_rate"
    ] = invalid_ratio

    completed, _ = _run_verifier(tmp_path, report)

    assert completed.returncode != 0
    assert expected_message in _combined_output(completed).lower()


def test_verifier_retains_report_but_rejects_match_rate_below_threshold(
    tmp_path: Path,
) -> None:
    report = _valid_report()
    report["validated_system"][  # type: ignore[index]
        "expected_status_match_rate"
    ] = {"numerator": 7, "denominator": 9, "rate": 0.778}

    completed, _ = _run_verifier(tmp_path, report)

    assert completed.returncode != 0
    assert "0.8" in _combined_output(completed)
    assert (tmp_path / "local_model-results.json").is_file()
    assert (tmp_path / "LOCAL_MODEL_EVAL_REPORT.md").is_file()
    assert '"status":"passed"' not in completed.stdout


def test_verifier_requires_nonzero_terminal_denominator(tmp_path: Path) -> None:
    report = _valid_report()
    validated = report["validated_system"]
    validated["job_count"] = 0  # type: ignore[index]
    validated["effective_output_rate"] = {  # type: ignore[index]
        "numerator": 0,
        "denominator": 0,
        "rate": None,
    }
    validated["terminal_distribution"] = {  # type: ignore[index]
        "actionable": 0,
        "needs_evidence": 0,
        "refused": 0,
        "failed": 0,
    }

    completed, _ = _run_verifier(tmp_path, report)

    assert completed.returncode != 0
    assert "denominator" in _combined_output(completed).lower()


def test_verifier_rejects_zero_effective_outputs(tmp_path: Path) -> None:
    report = _valid_report()
    validated = report["validated_system"]
    validated["effective_output_rate"] = {  # type: ignore[index]
        "numerator": 0,
        "denominator": 9,
        "rate": 0.0,
    }
    validated["terminal_distribution"] = {  # type: ignore[index]
        "actionable": 0,
        "needs_evidence": 0,
        "refused": 8,
        "failed": 1,
    }

    completed, _ = _run_verifier(tmp_path, report)

    assert completed.returncode != 0
    assert "effective" in _combined_output(completed).lower()


def test_verifier_rejects_all_failed_or_refused_with_forged_effective_count(
    tmp_path: Path,
) -> None:
    report = _valid_report()
    validated = report["validated_system"]
    validated["effective_output_rate"] = {  # type: ignore[index]
        "numerator": 1,
        "denominator": 9,
        "rate": 0.111,
    }
    validated["terminal_distribution"] = {  # type: ignore[index]
        "actionable": 0,
        "needs_evidence": 0,
        "refused": 8,
        "failed": 1,
    }

    completed, _ = _run_verifier(tmp_path, report)

    assert completed.returncode != 0
    assert "effective" in _combined_output(completed).lower()


def test_verifier_accepts_valid_report_and_prints_only_safe_summary(
    tmp_path: Path,
) -> None:
    completed, audit_path = _run_verifier(tmp_path, _valid_report())

    assert completed.returncode == 0, _combined_output(completed)
    assert completed.stderr == ""
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    assert len(lines) == 1
    summary = json.loads(lines[0])
    assert summary == {
        "status": "passed",
        "model": "qwen3.5:9b",
        "digest": "sha256:verified-local-artifact",
        "case_count": 3,
        "repeat_count": 3,
        "job_count": 9,
        "effective_output_count": 6,
        "provider_attempt_count": 18,
    }
    assert "SECRET" not in _combined_output(completed)

    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    assert audit["cases_flag"] == "--cases"
    assert audit["output_json_flag"] == "--output-json"
    assert audit["output_markdown_flag"] == "--output-markdown"
    assert audit["repeats_flag"] == "--repeats"
    assert audit["repeats_value"] == "3"
    assert audit["local_model_environment"] == EXPECTED_RUNNER_ENVIRONMENT
    assert "11434" not in _combined_output(completed)


def test_verifier_runs_runner_from_project_root_when_called_elsewhere(
    tmp_path: Path,
) -> None:
    completed, audit_path = _run_verifier(tmp_path, _valid_report())

    assert completed.returncode == 0, _combined_output(completed)
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    assert Path(audit["working_directory"]).resolve() == ROOT.resolve()


def test_verifier_supplies_all_runner_settings_without_env_file(
    tmp_path: Path,
) -> None:
    assert not (tmp_path / ".env").exists()

    completed, audit_path = _run_verifier(tmp_path, _valid_report())

    assert completed.returncode == 0, _combined_output(completed)
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    assert audit["local_model_environment"] == EXPECTED_RUNNER_ENVIRONMENT


def test_fast_local_verifier_does_not_run_real_model_evaluation() -> None:
    source = VERIFY_LOCAL_PATH.read_text(encoding="utf-8").lower()

    assert "verify_real_model" not in source
    assert "run_local_model_eval" not in source
