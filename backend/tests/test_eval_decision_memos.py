"""Offline regression checks for the synthetic decision-memo gate set."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
RUN_EVAL_PATH = ROOT / "eval" / "run_eval.py"
GOLD_MEMO_PATH = ROOT / "eval" / "gold_decision_memos.jsonl"
GOLD_REVIEW_PATH = ROOT / "eval" / "gold_reviews.jsonl"
EVAL_REPORT_PATH = ROOT / "docs" / "EVAL_REPORT.md"
VERIFY_LOCAL_PATH = ROOT / "scripts" / "verify_local.ps1"
VERIFY_MODEL_PATH = ROOT / "scripts" / "verify_model.ps1"


def _load_run_eval() -> ModuleType:
    spec = importlib.util.spec_from_file_location("signalforge_run_eval", RUN_EVAL_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_decision_memo_eval_has_quality_metrics() -> None:
    result = _load_run_eval().evaluate_decision_memos(GOLD_MEMO_PATH)

    assert result["unsupported_numeric_rate"] == 0.0
    assert result["decision_status_accuracy"] >= 0.8
    assert set(result) >= {
        "subproblem_evidence_precision",
        "citation_completeness",
        "unsupported_numeric_rate",
        "decision_status_accuracy",
        "generation_outcome_accuracy",
        "evidence_plan_specificity",
    }
    assert result["decision_status_case_count"] == 6
    assert result["generation_outcome_accuracy"] == 1.0
    json.dumps(result, ensure_ascii=False)


def test_review_eval_computes_unsupported_claim_rate_from_predictions() -> None:
    result = _load_run_eval().evaluate_reviews(GOLD_REVIEW_PATH)

    assert result["evidence_precision"] == 0.857
    assert result["unsupported_claim_rate"] == 0.143


def test_decision_memo_fixture_separates_expected_outputs_and_covers_safety_cases() -> None:
    rows = [
        json.loads(line)
        for line in GOLD_MEMO_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    tags = {tag for row in rows for tag in row["case_tags"]}

    assert {
        "actionable",
        "distributed_needs_evidence",
        "refused",
        "counter_evidence_omission",
        "redaction",
        "unknown_citation",
        "numeric_mismatch",
    }.issubset(tags)
    assert any(row["gold"] != row["prediction"] for row in rows)
    unknown_citation = next(row for row in rows if row["case_id"] == "dm06-unknown-citation")
    assert unknown_citation["gold"]["decision_status"] is None
    assert unknown_citation["prediction"]["decision_status"] is None
    assert unknown_citation["gold"]["job_outcome"] == "failed"
    assert unknown_citation["prediction"]["job_outcome"] == "failed"


def test_eval_artifacts_describe_synthetic_unvalidated_cases_honestly() -> None:
    implementation = RUN_EVAL_PATH.read_text(encoding="utf-8")
    report = EVAL_REPORT_PATH.read_text(encoding="utf-8")
    combined = f"{implementation}\n{report}".lower()

    assert "人工审核" not in combined
    assert "human-reviewed" not in combined
    assert "independently reviewed" not in combined
    assert "合成" in report
    assert "尚未完成独立" in report
    assert "不会执行生产验证器" in report


def test_decision_memo_metrics_penalize_unsupported_candidate_output(tmp_path: Path) -> None:
    case = {
        "case_id": "metric-sentinel",
        "case_tags": ["actionable"],
        "authoritative": {
            "numeric_facts": {"review_count": 2},
        },
        "gold": {
            "job_outcome": "completed",
            "decision_status": "actionable",
            "subproblems": [
                {"name": "响应时效", "supporting_evidence_ids": ["e1", "e2"]}
            ],
            "required_citation_ids": ["e1", "e2"],
            "evidence_plan": None,
        },
        "prediction": {
            "job_outcome": "failed",
            "decision_status": None,
            "subproblems": [
                {"name": "响应时效", "supporting_evidence_ids": ["e1", "e3"]}
            ],
            "citation_ids": ["e1"],
            "numeric_claims": [{"field": "review_count", "value": 99}],
            "evidence_plan": None,
        },
    }
    fixture = tmp_path / "decision-memos.jsonl"
    fixture.write_text(json.dumps(case, ensure_ascii=False) + "\n", encoding="utf-8")

    result = _load_run_eval().evaluate_decision_memos(fixture)

    assert result["subproblem_evidence_precision"] == 0.5
    assert result["citation_completeness"] == 0.5
    assert result["unsupported_numeric_rate"] == 1.0
    assert result["decision_status_accuracy"] == 0.0
    assert result["generation_outcome_accuracy"] == 0.0
    assert result["evidence_plan_specificity"] == 0.0


def _powershell() -> str:
    executable = shutil.which("powershell") or shutil.which("pwsh")
    if executable is None:
        pytest.skip("PowerShell is required for delivery-script runtime tests")
    return executable


def _ps_quote(value: Path | str) -> str:
    return str(value).replace("'", "''")


def _run_powershell_harness(tmp_path: Path, body: str) -> subprocess.CompletedProcess[str]:
    harness = tmp_path / "verify-harness.ps1"
    harness.write_text(
        "$OutputEncoding = [Console]::OutputEncoding = "
        "[System.Text.UTF8Encoding]::new($false)\n" + body,
        encoding="utf-8",
    )
    return subprocess.run(
        [
            _powershell(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(harness),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
    )


def _evaluation_result(**overrides: float) -> dict[str, float]:
    result = {
        "subproblem_evidence_precision": 0.875,
        "citation_completeness": 0.929,
        "unsupported_numeric_rate": 0.0,
        "decision_status_accuracy": 1.0,
        "evidence_plan_specificity": 0.875,
    }
    result.update(overrides)
    return result


def _write_freshness_inputs(
    tmp_path: Path,
    result: dict[str, float],
    *,
    stale: bool = False,
) -> tuple[Path, Path, Path, Path]:
    run_eval = tmp_path / "run_eval.py"
    gold_reviews = tmp_path / "gold_reviews.jsonl"
    gold_memos = tmp_path / "gold_decision_memos.jsonl"
    results = tmp_path / "results.json"
    run_eval.write_text("# synthetic evaluator\n", encoding="utf-8")
    gold_reviews.write_text("{}\n", encoding="utf-8")
    gold_memos.write_text("{}\n", encoding="utf-8")
    results.write_text(json.dumps(result), encoding="utf-8")
    baseline = time.time() - 120
    for source in (run_eval, gold_reviews, gold_memos):
        os.utime(source, (baseline, baseline))
    result_time = baseline - 10 if stale else baseline + 10
    os.utime(results, (result_time, result_time))
    return results, run_eval, gold_reviews, gold_memos


def _local_verifier_harness(
    results: Path,
    run_eval: Path,
    gold_reviews: Path,
    gold_memos: Path,
) -> str:
    return f"""
$global:auditTimeouts = @()
function Invoke-RestMethod {{
  param([Parameter(Position=0)][string]$Uri, [int]$TimeoutSec)
  $global:auditTimeouts += $TimeoutSec
  if ($Uri.EndsWith('/healthz/model')) {{
    return [pscustomobject]@{{
      configured=$false
      ready=$false
      provider='ollama'
      model_name=$null
      error_code='LOCAL_MODEL_UNAVAILABLE'
    }}
  }}
  return [pscustomobject]@{{status='ok'; database='ready'}}
}}
function Invoke-WebRequest {{
  param([switch]$UseBasicParsing, [Parameter(Position=0)][string]$Uri, [int]$TimeoutSec)
  $global:auditTimeouts += $TimeoutSec
  return [pscustomobject]@{{StatusCode=200}}
}}
& '{_ps_quote(VERIFY_LOCAL_PATH)}' `
  -ApiBaseUrl 'http://local.invalid' `
  -WebBaseUrl 'http://web.invalid' `
  -ResultsPath '{_ps_quote(results)}' `
  -EvalScriptPath '{_ps_quote(run_eval)}' `
  -GoldReviewsPath '{_ps_quote(gold_reviews)}' `
  -GoldDecisionMemosPath '{_ps_quote(gold_memos)}' `
  -RequestTimeoutSeconds 7
if ($global:auditTimeouts.Count -ne 3 -or `
    @($global:auditTimeouts | Where-Object {{ $_ -ne 7 }}).Count -ne 0) {{
  throw 'Every mocked HTTP call must receive TimeoutSec=7.'
}}
"""


def test_verify_local_accepts_fresh_results_and_bounds_every_http_call(
    tmp_path: Path,
) -> None:
    inputs = _write_freshness_inputs(tmp_path, _evaluation_result())

    completed = _run_powershell_harness(tmp_path, _local_verifier_harness(*inputs))

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "Local verification passed" in completed.stdout


def test_verify_local_rejects_stale_evaluation_results(tmp_path: Path) -> None:
    inputs = _write_freshness_inputs(tmp_path, _evaluation_result(), stale=True)

    completed = _run_powershell_harness(tmp_path, _local_verifier_harness(*inputs))

    assert completed.returncode != 0
    assert "stale" in (completed.stdout + completed.stderr).lower()


@pytest.mark.parametrize(
    ("metric", "failing_value"),
    [
        ("subproblem_evidence_precision", 0.74),
        ("citation_completeness", 0.84),
        ("unsupported_numeric_rate", 0.01),
        ("decision_status_accuracy", 0.79),
        ("evidence_plan_specificity", 0.74),
    ],
)
def test_verify_local_enforces_all_decision_quality_gates(
    tmp_path: Path,
    metric: str,
    failing_value: float,
) -> None:
    inputs = _write_freshness_inputs(
        tmp_path,
        _evaluation_result(**{metric: failing_value}),
    )

    completed = _run_powershell_harness(tmp_path, _local_verifier_harness(*inputs))

    assert completed.returncode != 0
    assert metric in completed.stdout + completed.stderr


def test_verify_model_bounds_its_mocked_health_request(tmp_path: Path) -> None:
    body = f"""
$global:auditTimeouts = @()
function Invoke-RestMethod {{
  param([Parameter(Position=0)][string]$Uri, [int]$TimeoutSec)
  $global:auditTimeouts += $TimeoutSec
  return [pscustomobject]@{{
    configured=$false
    ready=$false
    provider='ollama'
    model_name=$null
    error_code='LOCAL_MODEL_UNAVAILABLE'
  }}
}}
& '{_ps_quote(VERIFY_MODEL_PATH)}' `
  -ApiBaseUrl 'http://local.invalid' `
  -RequestTimeoutSeconds 9
if ($global:auditTimeouts.Count -ne 1 -or $global:auditTimeouts[0] -ne 9) {{
  throw 'The mocked model-health call must receive TimeoutSec=9.'
}}
"""

    completed = _run_powershell_harness(tmp_path, body)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "non-fatal development state" in completed.stdout


def test_local_model_delivery_configuration_is_read_only_and_no_download() -> None:
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    model_verifier = (ROOT / "scripts" / "verify_model.ps1").read_text(
        encoding="utf-8"
    )
    local_verifier = (ROOT / "scripts" / "verify_local.ps1").read_text(
        encoding="utf-8"
    )

    assert "LOCAL_MODEL_BASE_URL=http://host.docker.internal:11434" in env_example
    assert "LOCAL_MODEL_NAME=" in env_example
    assert "LOCAL_MODEL_TIMEOUT_SECONDS=120" in env_example
    assert "host.docker.internal:host-gateway" in compose
    assert model_verifier.count("Invoke-RestMethod") == 1
    assert '"$ApiBaseUrl/healthz/model"' in model_verifier
    assert all(
        forbidden not in model_verifier.lower()
        for forbidden in ("docker pull", "ollama pull", "install-package", "/api/chat")
    )
    assert '"http://localhost:3000"' in local_verifier
    assert '"$ApiBaseUrl/healthz/model"' in local_verifier
