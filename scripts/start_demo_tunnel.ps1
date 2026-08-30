param(
  [string]$CloudflaredPath = "cloudflared",
  [string]$WebBaseUrl = "http://localhost:3000",
  [string]$StatePath = "",
  [string]$LogPath = "",
  [ValidateRange(1, 300)]
  [int]$StartupTimeoutSeconds = 30
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($StatePath)) {
  $StatePath = Join-Path $env:TEMP "signalforge-demo-tunnel.json"
}
if ([string]::IsNullOrWhiteSpace($LogPath)) {
  $LogPath = Join-Path $env:TEMP "signalforge-demo-tunnel.log"
}
$ErrorLogPath = "$LogPath.stderr"

function Ensure-ParentDirectory([string]$Path) {
  $parent = Split-Path -Parent -Path $Path
  if (-not [string]::IsNullOrWhiteSpace($parent) -and -not (Test-Path -LiteralPath $parent)) {
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
  }
}

function Read-State([string]$Path) {
  if (-not (Test-Path -LiteralPath $Path)) {
    return $null
  }
  try {
    return Get-Content -Raw -Encoding UTF8 -LiteralPath $Path | ConvertFrom-Json -ErrorAction Stop
  }
  catch {
    Remove-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
    return $null
  }
}

function Test-TunnelProcessIdentity($Process, $State) {
  $stored = [string]$State.executable
  if ([string]::IsNullOrWhiteSpace($stored)) {
    return $false
  }
  $storedLeaf = [System.IO.Path]::GetFileNameWithoutExtension((Split-Path -Leaf $stored))
  $processName = [string]$Process.ProcessName
  if ([System.IO.Path]::IsPathRooted($stored)) {
    try {
      $processPath = [string]$Process.Path
      if (-not [string]::IsNullOrWhiteSpace($processPath)) {
        return ([System.IO.Path]::GetFullPath($processPath)).Equals(
          [System.IO.Path]::GetFullPath($stored),
          [System.StringComparison]::OrdinalIgnoreCase
        )
      }
    }
    catch { }
    return $false
  }
  return (-not [string]::IsNullOrWhiteSpace($processName) -and
    $processName.Equals($storedLeaf, [System.StringComparison]::OrdinalIgnoreCase))
}

function Get-CloudflareUrl([string]$Text) {
  # Only accept the hostname Cloudflare Quick Tunnels promise; reject paths,
  # query strings, look-alike domains, and non-HTTPS URLs.
  $pattern = '(?<![A-Za-z0-9.-])https://[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.trycloudflare\.com/?(?![A-Za-z0-9./-])'
  $match = [regex]::Match($Text, $pattern, [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)
  if ($match.Success) {
    return $match.Value.TrimEnd('/')
  }
  return $null
}

try {
  $webResponse = Invoke-WebRequest -UseBasicParsing -Uri $WebBaseUrl -TimeoutSec 10
  if ($webResponse.StatusCode -lt 200 -or $webResponse.StatusCode -ge 400) {
    throw "Web application at $WebBaseUrl did not return a successful response."
  }

  $existing = Read-State $StatePath
  if ($null -ne $existing -and $existing.pid -is [int]) {
    $existingProcess = Get-Process -Id $existing.pid -ErrorAction SilentlyContinue
    if ($null -ne $existingProcess -and -not $existingProcess.HasExited -and
        (Test-TunnelProcessIdentity $existingProcess $existing)) {
      Write-Output ("Demo tunnel already running: {0}" -f $existing.url)
      exit 0
    }
    Remove-Item -LiteralPath $StatePath -Force -ErrorAction SilentlyContinue
  }

  Ensure-ParentDirectory $StatePath
  Ensure-ParentDirectory $LogPath
  Ensure-ParentDirectory $ErrorLogPath
  Remove-Item -LiteralPath $LogPath, $ErrorLogPath -Force -ErrorAction SilentlyContinue

  $arguments = @("tunnel", "--url", $WebBaseUrl, "--no-autoupdate")
  $tunnelProcess = Start-Process `
    -FilePath $CloudflaredPath `
    -ArgumentList $arguments `
    -RedirectStandardOutput $LogPath `
    -RedirectStandardError $ErrorLogPath `
    -WindowStyle Hidden `
    -PassThru

  $deadline = (Get-Date).AddSeconds($StartupTimeoutSeconds)
  $publicUrl = $null
  while ((Get-Date) -lt $deadline) {
    if ($tunnelProcess.HasExited) {
      throw "cloudflared exited before publishing a Quick Tunnel URL."
    }
    $outputText = ""
    if (Test-Path -LiteralPath $LogPath) {
      $outputText += Get-Content -Raw -Encoding UTF8 -LiteralPath $LogPath
    }
    if (Test-Path -LiteralPath $ErrorLogPath) {
      $outputText += Get-Content -Raw -Encoding UTF8 -LiteralPath $ErrorLogPath
    }
    if (-not [string]::IsNullOrWhiteSpace($outputText)) {
      $publicUrl = Get-CloudflareUrl $outputText
      if ($null -ne $publicUrl) {
        break
      }
    }
    Start-Sleep -Milliseconds 250
  }
  if ($null -eq $publicUrl) {
    throw "Timed out waiting for a strict https://*.trycloudflare.com URL. See $LogPath."
  }

  $state = [ordered]@{
    pid = [int]$tunnelProcess.Id
    url = $publicUrl
    web_base_url = $WebBaseUrl
    started_at_utc = (Get-Date).ToUniversalTime().ToString("o")
    log_path = $LogPath
    executable = $CloudflaredPath
  }
  $state | ConvertTo-Json -Depth 3 | Set-Content -Encoding UTF8 -LiteralPath $StatePath
  Write-Output ("Demo tunnel ready: {0}" -f $publicUrl)
}
catch {
  if ($null -ne $tunnelProcess) {
    try {
      if (-not $tunnelProcess.HasExited) {
        Stop-Process -Id ([int]$tunnelProcess.Id) -Force -ErrorAction SilentlyContinue
      }
    }
    catch { }
  }
  Remove-Item -LiteralPath $StatePath -Force -ErrorAction SilentlyContinue
  throw
}
