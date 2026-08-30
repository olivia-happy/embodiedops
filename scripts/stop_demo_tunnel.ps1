param(
  [string]$StatePath = ""
)

$ErrorActionPreference = "Stop"
if ([string]::IsNullOrWhiteSpace($StatePath)) {
  $StatePath = Join-Path $env:TEMP "signalforge-demo-tunnel.json"
}

if (-not (Test-Path -LiteralPath $StatePath)) {
  Write-Output "No active demo tunnel."
  exit 0
}

$removeState = $false
try {
  $state = Get-Content -Raw -Encoding UTF8 -LiteralPath $StatePath | ConvertFrom-Json -ErrorAction Stop
  $pidValue = 0
  if (-not [int]::TryParse([string]$state.pid, [ref]$pidValue) -or $pidValue -le 0) {
    throw "Tunnel state does not contain a valid PID."
  }
  $tunnelProcess = Get-Process -Id $pidValue -ErrorAction SilentlyContinue
  if ($null -ne $tunnelProcess) {
    # Stop exactly the PID recorded by start_demo_tunnel.ps1; never use a
    # name-based or wildcard process termination.
    $storedExecutable = [string]$state.executable
    $storedLeaf = [System.IO.Path]::GetFileNameWithoutExtension((Split-Path -Leaf $storedExecutable))
    $processName = [string]$tunnelProcess.ProcessName
    $identityMatches = $false
    if ([System.IO.Path]::IsPathRooted($storedExecutable)) {
      try {
        $processPath = [string]$tunnelProcess.Path
        $identityMatches = (-not [string]::IsNullOrWhiteSpace($processPath) -and
          [System.IO.Path]::GetFullPath($processPath).Equals(
            [System.IO.Path]::GetFullPath($storedExecutable),
            [System.StringComparison]::OrdinalIgnoreCase
          ))
      }
      catch { }
    }
    else {
      $identityMatches = (-not [string]::IsNullOrWhiteSpace($storedLeaf) -and
        $processName.Equals($storedLeaf, [System.StringComparison]::OrdinalIgnoreCase))
    }
    if (-not $identityMatches) {
      throw "Recorded tunnel PID $pidValue is not the expected cloudflared executable; refusing to stop it."
    }
    Stop-Process -Id $pidValue -Force -ErrorAction Stop
    Write-Output ("Stopped demo tunnel process {0}." -f $pidValue)
    $removeState = $true
  }
  else {
    Write-Output ("Demo tunnel process {0} is already stopped." -f $pidValue)
    $removeState = $true
  }
}
finally {
  if ($removeState) {
    Remove-Item -LiteralPath $StatePath -Force -ErrorAction SilentlyContinue
  }
}
