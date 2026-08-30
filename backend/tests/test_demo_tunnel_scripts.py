from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
START_SCRIPT = ROOT / "scripts" / "start_demo_tunnel.ps1"
STOP_SCRIPT = ROOT / "scripts" / "stop_demo_tunnel.ps1"
README_PATH = ROOT / "README.md"


def _powershell() -> str:
    executable = shutil.which("powershell") or shutil.which("pwsh")
    if executable is None:
        pytest.skip("PowerShell is required for demo tunnel script tests")
    return executable


def _ps_quote(value: Path | str) -> str:
    return str(value).replace("'", "''")


def _run_harness(path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            _powershell(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(path),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
    )


def test_start_script_uses_hidden_process_and_strict_url_contract() -> None:
    source = START_SCRIPT.read_text(encoding="utf-8")
    assert "Start-Process" in source
    assert "-WindowStyle Hidden" in source
    assert "--no-autoupdate" in source
    assert "trycloudflare\\.com" in source
    assert "https://" in source
    assert "localhost:3000" in source


def test_readme_documents_temporary_read_only_tunnel_boundary() -> None:
    readme = README_PATH.read_text(encoding="utf-8")
    assert "Cloudflare Quick Tunnel" in readme
    assert "不提供 SLA" in readme
    assert "11434" in readme
    assert "只读" in readme
    assert "stop_demo_tunnel.ps1" in readme


def test_start_script_records_exact_pid_and_url_without_real_tunnel(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    log_path = tmp_path / "cloudflared.log"
    audit_path = tmp_path / "start-audit.json"
    harness_path = tmp_path / "start-harness.ps1"
    harness_path.write_text(
        f"""$ErrorActionPreference = 'Stop'
function Invoke-WebRequest {{
  param([string]$Uri, [string]$Method, [int]$TimeoutSec, [switch]$UseBasicParsing)
  return [pscustomobject]@{{ StatusCode = 200 }}
}}
function Start-Process {{
  param(
    [string]$FilePath,
    [object[]]$ArgumentList,
    [string]$RedirectStandardOutput,
    [string]$RedirectStandardError,
    [string]$WindowStyle,
    [switch]$PassThru
  )
  [System.IO.File]::WriteAllText($RedirectStandardOutput,
    'INF https://demo-abc.trycloudflare.com`n')
  [System.IO.File]::WriteAllText($RedirectStandardError, '')
  [System.IO.File]::WriteAllText(
    '{_ps_quote(audit_path)}',
    ($PSBoundParameters | ConvertTo-Json -Compress)
  )
  return [pscustomobject]@{{
    Id = 4242
    HasExited = $false
    StartTime = Get-Date
  }}
}}
function Get-Process {{
  param([int]$Id, [string]$ErrorAction)
  return $null
}}
function Stop-Process {{
  param([int]$Id, [switch]$Force, [string]$ErrorAction)
  throw 'Stop-Process should not be called for a new process.'
}}
& '{_ps_quote(START_SCRIPT)}' `
  -CloudflaredPath 'fake-cloudflared' `
  -WebBaseUrl 'http://localhost:3000' `
  -StatePath '{_ps_quote(state_path)}' `
  -LogPath '{_ps_quote(log_path)}' `
  -StartupTimeoutSeconds 2
""",
        encoding="utf-8",
    )

    completed = _run_harness(harness_path)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    state = json.loads(state_path.read_text(encoding="utf-8-sig"))
    assert state["pid"] == 4242
    assert state["url"] == "https://demo-abc.trycloudflare.com"
    audit = json.loads(audit_path.read_text(encoding="utf-8-sig"))
    assert audit["FilePath"] == "fake-cloudflared"
    assert audit["WindowStyle"] == "Hidden"
    assert "--no-autoupdate" in audit["ArgumentList"]
    assert "http://localhost:3000" in audit["ArgumentList"]


def test_start_script_rejects_non_trycloudflare_urls(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    log_path = tmp_path / "cloudflared.log"
    harness_path = tmp_path / "reject-harness.ps1"
    harness_path.write_text(
        f"""$ErrorActionPreference = 'Stop'
function Invoke-WebRequest {{
  param([string]$Uri, [string]$Method, [int]$TimeoutSec, [switch]$UseBasicParsing)
  return [pscustomobject]@{{ StatusCode = 200 }}
}}
function Start-Process {{
  param([string]$RedirectStandardOutput, [string]$RedirectStandardError)
  [System.IO.File]::WriteAllText($RedirectStandardOutput,
    'INF https://demo.trycloudflare.com/unsafe/path`n')
  [System.IO.File]::WriteAllText($RedirectStandardError, '')
  return [pscustomobject]@{{ Id = 4343; HasExited = $false; StartTime = Get-Date }}
}}
function Get-Process {{ param([int]$Id, [string]$ErrorAction) return $null }}
function Stop-Process {{ param([int]$Id, [switch]$Force) }}
& '{_ps_quote(START_SCRIPT)}' `
  -CloudflaredPath 'fake-cloudflared' `
  -WebBaseUrl 'http://localhost:3000' `
  -StatePath '{_ps_quote(state_path)}' `
  -LogPath '{_ps_quote(log_path)}' `
  -StartupTimeoutSeconds 1
""",
        encoding="utf-8",
    )

    completed = _run_harness(harness_path)

    assert completed.returncode != 0
    assert "trycloudflare" in (completed.stdout + completed.stderr).lower()
    assert not state_path.exists()


def test_stop_script_targets_only_recorded_pid_and_is_idempotent(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    audit_path = tmp_path / "stop-audit.json"
    state_path.write_text(
        json.dumps(
            {
                "pid": 5151,
                "url": "https://demo-abc.trycloudflare.com",
                "executable": "cloudflared",
                "started_at_utc": "2026-08-10T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    harness_path = tmp_path / "stop-harness.ps1"
    harness_path.write_text(
        f"""$ErrorActionPreference = 'Stop'
function Get-Process {{
  param([int]$Id, [string]$ErrorAction)
  if ($Id -eq 5151) {{ return [pscustomobject]@{{ Id = $Id; ProcessName = 'cloudflared' }} }}
  return $null
}}
function Stop-Process {{
  param([int]$Id, [switch]$Force, [string]$ErrorAction)
  [System.IO.File]::WriteAllText('{_ps_quote(audit_path)}',
    ($PSBoundParameters | ConvertTo-Json -Compress))
}}
& '{_ps_quote(STOP_SCRIPT)}' -StatePath '{_ps_quote(state_path)}'
if (Test-Path -LiteralPath '{_ps_quote(state_path)}') {{ throw 'state was not removed' }}
& '{_ps_quote(STOP_SCRIPT)}' -StatePath '{_ps_quote(state_path)}'
""",
        encoding="utf-8",
    )

    completed = _run_harness(harness_path)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    audit = json.loads(audit_path.read_text(encoding="utf-8-sig"))
    assert audit["Id"] == 5151
    assert audit["Force"]["IsPresent"] is True


def test_stop_script_fails_closed_on_pid_reuse(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    audit_path = tmp_path / "stop-audit.json"
    state_path.write_text(
        json.dumps(
            {
                "pid": 6161,
                "url": "https://demo-abc.trycloudflare.com",
                "executable": "cloudflared",
            }
        ),
        encoding="utf-8",
    )
    harness_path = tmp_path / "pid-reuse-harness.ps1"
    harness_path.write_text(
        f"""$ErrorActionPreference = 'Stop'
function Get-Process {{
  param([int]$Id, [string]$ErrorAction)
  return [pscustomobject]@{{ Id = $Id; ProcessName = 'notepad' }}
}}
function Stop-Process {{
  param([int]$Id, [switch]$Force, [string]$ErrorAction)
  [System.IO.File]::WriteAllText('{_ps_quote(audit_path)}', 'MUST_NOT_STOP')
}}
& '{_ps_quote(STOP_SCRIPT)}' -StatePath '{_ps_quote(state_path)}'
""",
        encoding="utf-8",
    )

    completed = _run_harness(harness_path)

    assert completed.returncode != 0
    assert "refusing" in (completed.stdout + completed.stderr).lower()
    assert state_path.exists()
    assert not audit_path.exists()
