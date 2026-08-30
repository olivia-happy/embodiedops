[CmdletBinding()]
param(
    [string]$OutputRoot = "data/embodied/raw",
    [string]$PythonExecutable = ""
)

$ErrorActionPreference = "Stop"
$datasetVersion = "robomimic-lift-ph-low-dim-v1"
$sourceUrl = "https://downloads.cs.stanford.edu/downloads/rt_benchmark/lift/ph/low_dim.hdf5"
$rootPath = [System.IO.Path]::GetFullPath($OutputRoot)
$outputDir = Join-Path $rootPath "robomimic"
$artifact = Join-Path $outputDir "low_dim.hdf5"
$manifest = Join-Path $outputDir "manifest.json"

New-Item -ItemType Directory -Force -Path $outputDir | Out-Null
if (-not (Test-Path $artifact)) {
    Invoke-WebRequest -Uri $sourceUrl -OutFile $artifact
}

$sha256 = (Get-FileHash -LiteralPath $artifact -Algorithm SHA256).Hash.ToLowerInvariant()
if ([string]::IsNullOrWhiteSpace($PythonExecutable)) {
    $projectRoot = Split-Path -Parent $PSScriptRoot
    $PythonExecutable = Join-Path $projectRoot ".venv\Scripts\python.exe"
}
if (-not (Test-Path -LiteralPath $PythonExecutable)) {
    throw "Python with h5py is required. Run the project setup first or pass -PythonExecutable."
}
$episodeCount = & $PythonExecutable -c "import h5py; f=h5py.File(r'$artifact','r'); print(len(f['data'])); f.close()"
if ($LASTEXITCODE -ne 0 -or [int]$episodeCount -lt 1) {
    throw "Unable to read a non-empty Robomimic HDF5 data group."
}
$payload = @{
    dataset_version_id = $datasetVersion
    source_name = "robomimic_v0.1_lift_proficient_human_low_dim"
    source_url = $sourceUrl
    sha256 = $sha256
    data_type = "public_simulation"
    real_robot_data = $false
    task_id = "robomimic_lift"
    episode_count = [int]$episodeCount
    acquired_at_utc = [DateTime]::UtcNow.ToString("o")
} | ConvertTo-Json -Depth 3
$payload | Set-Content -LiteralPath $manifest -Encoding utf8
Write-Output "Robomimic public simulation artifact ready: $artifact"
