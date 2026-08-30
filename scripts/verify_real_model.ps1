[CmdletBinding()]
param(
  [string]$ApiBaseUrl = "http://localhost:8000",
  [ValidateSet("qwen3.5:9b")]
  [string]$ExpectedModel = "qwen3.5:9b",
  [ValidateRange(1, 20)]
  [int]$Repeats = 3,
  [ValidateRange(1, 1800)]
  [int]$RequestTimeoutSeconds = 120,
  [string]$PythonExecutable = "",
  [string]$RunnerPath = "",
  [string]$CasesPath = "",
  [string]$ResultsPath = "",
  [string]$MarkdownReportPath = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
if ([string]::IsNullOrWhiteSpace($PythonExecutable)) {
  $PythonExecutable = Join-Path $projectRoot ".venv\Scripts\python.exe"
}
if ([string]::IsNullOrWhiteSpace($RunnerPath)) {
  $RunnerPath = Join-Path $projectRoot "eval\run_local_model_eval.py"
}
if ([string]::IsNullOrWhiteSpace($CasesPath)) {
  $CasesPath = Join-Path $projectRoot "eval\live_model_cases.jsonl"
}
if ([string]::IsNullOrWhiteSpace($ResultsPath)) {
  $ResultsPath = Join-Path $projectRoot "eval\local_model-results.json"
}
if ([string]::IsNullOrWhiteSpace($MarkdownReportPath)) {
  $MarkdownReportPath = Join-Path $projectRoot "docs\LOCAL_MODEL_EVAL_REPORT.md"
}

function Get-RequiredProperty {
  param(
    [Parameter(Mandatory = $true)]
    [AllowNull()]
    [object]$InputObject,
    [Parameter(Mandatory = $true)]
    [string]$Name,
    [Parameter(Mandatory = $true)]
    [string]$Context
  )

  if ($null -eq $InputObject) {
    throw "Real-model result is missing $Context."
  }
  $property = $InputObject.PSObject.Properties[$Name]
  if ($null -eq $property) {
    throw "Real-model result is missing $Context.$Name."
  }
  return $property.Value
}

function Get-RequiredSection {
  param(
    [Parameter(Mandatory = $true)]
    [object]$InputObject,
    [Parameter(Mandatory = $true)]
    [string]$Name,
    [Parameter(Mandatory = $true)]
    [string]$Context
  )

  $section = Get-RequiredProperty `
    -InputObject $InputObject `
    -Name $Name `
    -Context $Context
  if (
    $null -eq $section -or
    $section -is [string] -or
    $section -is [System.ValueType] -or
    $section -is [System.Collections.IList]
  ) {
    throw "Real-model result section $Context.$Name is invalid."
  }
  return $section
}

function ConvertTo-NonNegativeInteger {
  param(
    [Parameter(Mandatory = $true)]
    [AllowNull()]
    [object]$Value,
    [Parameter(Mandatory = $true)]
    [string]$Name
  )

  if ($null -eq $Value -or $Value -is [bool] -or $Value -is [string]) {
    throw "Real-model result field $Name must be a non-negative integer."
  }
  try {
    $number = [decimal]$Value
  }
  catch {
    throw "Real-model result field $Name must be a non-negative integer."
  }
  if (
    $number -lt 0 -or
    $number -ne [decimal]::Truncate($number) -or
    $number -gt [long]::MaxValue
  ) {
    throw "Real-model result field $Name must be a non-negative integer."
  }
  return [long]$number
}

function Confirm-ExactInteger {
  param(
    [Parameter(Mandatory = $true)]
    [AllowNull()]
    [object]$Value,
    [Parameter(Mandatory = $true)]
    [long]$Expected,
    [Parameter(Mandatory = $true)]
    [string]$Name
  )

  if (
    $null -eq $Value -or
    ($Value.GetType() -ne [int] -and $Value.GetType() -ne [long]) -or
    [long]$Value -ne $Expected
  ) {
    throw "Real-model configuration field $Name must be integer $Expected."
  }
}

function Confirm-ExactNumber {
  param(
    [Parameter(Mandatory = $true)]
    [AllowNull()]
    [object]$Value,
    [Parameter(Mandatory = $true)]
    [decimal]$Expected,
    [Parameter(Mandatory = $true)]
    [string]$Name
  )

  $allowedTypes = @([int], [long], [double], [decimal])
  if ($null -eq $Value -or $allowedTypes -notcontains $Value.GetType()) {
    throw "Real-model configuration field $Name must be numeric $Expected."
  }
  try {
    $number = [decimal]$Value
  }
  catch {
    throw "Real-model configuration field $Name must be numeric $Expected."
  }
  if ($number -ne $Expected) {
    throw "Real-model configuration field $Name must equal $Expected."
  }
}

function ConvertTo-FiniteNumber {
  param(
    [Parameter(Mandatory = $true)]
    [AllowNull()]
    [object]$Value,
    [Parameter(Mandatory = $true)]
    [string]$Name
  )

  $allowedTypes = @([int], [long], [double], [decimal])
  if ($null -eq $Value -or $allowedTypes -notcontains $Value.GetType()) {
    throw "Real-model result field $Name must be numeric."
  }
  try {
    $number = [double]$Value
  }
  catch {
    throw "Real-model result field $Name must be numeric."
  }
  if ([double]::IsNaN($number) -or [double]::IsInfinity($number)) {
    throw "Real-model result field $Name must be finite."
  }
  return $number
}

try {
  $apiUri = [System.Uri]$ApiBaseUrl
}
catch {
  throw "ApiBaseUrl must be a valid local HTTP URL."
}
if (
  -not $apiUri.IsAbsoluteUri -or
  $apiUri.Scheme -ne "http" -or
  -not $apiUri.IsLoopback -or
  -not [string]::IsNullOrEmpty($apiUri.UserInfo)
) {
  throw "ApiBaseUrl must be a loopback HTTP URL without credentials."
}

$normalizedApiBaseUrl = $ApiBaseUrl.TrimEnd([char]"/")
try {
  $health = Invoke-RestMethod `
    "$normalizedApiBaseUrl/healthz/model" `
    -TimeoutSec $RequestTimeoutSeconds
}
catch {
  throw "Real-model health request failed."
}

$configured = Get-RequiredProperty `
  -InputObject $health `
  -Name "configured" `
  -Context "health"
if ($configured -isnot [bool] -or -not $configured) {
  throw "Local model must be configured before real evaluation."
}
$ready = Get-RequiredProperty `
  -InputObject $health `
  -Name "ready" `
  -Context "health"
if ($ready -isnot [bool] -or -not $ready) {
  throw "Local model must be ready before real evaluation."
}
$healthModel = Get-RequiredProperty `
  -InputObject $health `
  -Name "model_name" `
  -Context "health"
if (
  $healthModel -isnot [string] -or
  -not [string]::Equals(
    $healthModel,
    $ExpectedModel,
    [System.StringComparison]::Ordinal
  )
) {
  throw "Local model health does not match the expected model."
}

foreach ($requiredFile in @($PythonExecutable, $RunnerPath, $CasesPath)) {
  if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
    throw "A required local evaluation file is missing."
  }
}

$LASTEXITCODE = 0
$runnerSucceeded = $false
$runnerExitCode = 1
$locationPushed = $false
$runnerEnvironment = [ordered]@{
  LOCAL_MODEL_BASE_URL = "http://localhost:11434"
  LOCAL_MODEL_NAME = "qwen3.5:9b"
  LOCAL_MODEL_TIMEOUT_SECONDS = "120"
  LOCAL_MODEL_CONTEXT_LENGTH = "16384"
  LOCAL_MODEL_TEMPERATURE = "0"
  LOCAL_MODEL_SEED = "42"
  LOCAL_MODEL_MAX_OUTPUT_TOKENS = "2048"
  LOCAL_MODEL_THINK = "true"
}
$previousEnvironment = @{}
$presentEnvironment = @{}
foreach ($name in $runnerEnvironment.Keys) {
  $previousValue = [System.Environment]::GetEnvironmentVariable(
    $name,
    [System.EnvironmentVariableTarget]::Process
  )
  $previousEnvironment[$name] = $previousValue
  $presentEnvironment[$name] = $null -ne $previousValue
}
try {
  try {
    Push-Location -LiteralPath $projectRoot
    $locationPushed = $true
    foreach ($entry in $runnerEnvironment.GetEnumerator()) {
      [System.Environment]::SetEnvironmentVariable(
        $entry.Key,
        $entry.Value,
        [System.EnvironmentVariableTarget]::Process
      )
    }
    & $PythonExecutable `
      $RunnerPath `
      "--cases" $CasesPath `
      "--output-json" $ResultsPath `
      "--output-markdown" $MarkdownReportPath `
      "--repeats" $Repeats *> $null
    $runnerSucceeded = $?
    $runnerExitCode = $LASTEXITCODE
  }
  catch {
    $runnerSucceeded = $false
  }
}
finally {
  try {
    foreach ($name in $runnerEnvironment.Keys) {
      $restoredValue = if ($presentEnvironment[$name]) {
        $previousEnvironment[$name]
      }
      else {
        $null
      }
      [System.Environment]::SetEnvironmentVariable(
        $name,
        $restoredValue,
        [System.EnvironmentVariableTarget]::Process
      )
    }
  }
  finally {
    if ($locationPushed) {
      Pop-Location
    }
  }
}
if (-not $runnerSucceeded -or $runnerExitCode -ne 0) {
  throw "Local-model evaluation runner failed."
}

if (-not (Test-Path -LiteralPath $ResultsPath -PathType Leaf)) {
  throw "Local-model evaluation runner did not produce a JSON result."
}
$resultsTimestamp = (Get-Item -LiteralPath $ResultsPath).LastWriteTimeUtc
foreach ($sourcePath in @($RunnerPath, $CasesPath)) {
  $sourceTimestamp = (Get-Item -LiteralPath $sourcePath).LastWriteTimeUtc
  if ($resultsTimestamp -le $sourceTimestamp) {
    throw "Local-model evaluation result is stale."
  }
}

try {
  $evaluation = Get-Content `
    -Raw `
    -Encoding UTF8 `
    -LiteralPath $ResultsPath | ConvertFrom-Json -ErrorAction Stop
}
catch {
  throw "Local-model evaluation result is not valid UTF-8 JSON."
}

$modelArtifact = Get-RequiredSection `
  -InputObject $evaluation `
  -Name "model_artifact" `
  -Context "result"
$configuration = Get-RequiredSection `
  -InputObject $evaluation `
  -Name "configuration" `
  -Context "result"
$rawModel = Get-RequiredSection `
  -InputObject $evaluation `
  -Name "raw_model" `
  -Context "result"
$validatedSystem = Get-RequiredSection `
  -InputObject $evaluation `
  -Name "validated_system" `
  -Context "result"
$runtime = Get-RequiredSection `
  -InputObject $evaluation `
  -Name "runtime" `
  -Context "result"

$configuredModel = Get-RequiredProperty `
  -InputObject $configuration `
  -Name "model_name" `
  -Context "configuration"
if (
  $configuredModel -isnot [string] -or
  -not [string]::Equals(
    $configuredModel,
    $ExpectedModel,
    [System.StringComparison]::Ordinal
  )
) {
  throw "Real-model configuration field model_name is invalid."
}
Confirm-ExactInteger `
  -Value (Get-RequiredProperty `
    -InputObject $configuration `
    -Name "context_length" `
    -Context "configuration") `
  -Expected 16384 `
  -Name "context_length"
Confirm-ExactNumber `
  -Value (Get-RequiredProperty `
    -InputObject $configuration `
    -Name "temperature" `
    -Context "configuration") `
  -Expected 0 `
  -Name "temperature"
Confirm-ExactInteger `
  -Value (Get-RequiredProperty `
    -InputObject $configuration `
    -Name "seed" `
    -Context "configuration") `
  -Expected 42 `
  -Name "seed"
Confirm-ExactInteger `
  -Value (Get-RequiredProperty `
    -InputObject $configuration `
    -Name "max_output_tokens" `
    -Context "configuration") `
  -Expected 2048 `
  -Name "max_output_tokens"
$reasoningEnabled = Get-RequiredProperty `
  -InputObject $configuration `
  -Name "reasoning_enabled" `
  -Context "configuration"
if ($reasoningEnabled -isnot [bool] -or -not $reasoningEnabled) {
  throw "Real-model configuration field reasoning_enabled must be true."
}
Confirm-ExactNumber `
  -Value (Get-RequiredProperty `
    -InputObject $configuration `
    -Name "timeout_seconds" `
    -Context "configuration") `
  -Expected 120 `
  -Name "timeout_seconds"

$artifactTag = Get-RequiredProperty `
  -InputObject $modelArtifact `
  -Name "tag" `
  -Context "model_artifact"
if (
  $artifactTag -isnot [string] -or
  -not [string]::Equals(
    $artifactTag,
    $ExpectedModel,
    [System.StringComparison]::Ordinal
  )
) {
  throw "Evaluated model artifact does not match the expected model."
}
$digest = Get-RequiredProperty `
  -InputObject $modelArtifact `
  -Name "digest" `
  -Context "model_artifact"
if ($digest -isnot [string] -or [string]::IsNullOrWhiteSpace($digest)) {
  throw "Evaluated model artifact digest must be non-empty."
}

$caseCount = ConvertTo-NonNegativeInteger `
  -Value (Get-RequiredProperty `
    -InputObject $evaluation `
    -Name "case_count" `
    -Context "result") `
  -Name "case_count"
$repeatCount = ConvertTo-NonNegativeInteger `
  -Value (Get-RequiredProperty `
    -InputObject $evaluation `
    -Name "repeat_count" `
    -Context "result") `
  -Name "repeat_count"
if ($caseCount -le 0) {
  throw "Real-model case denominator must be greater than zero."
}
if ($repeatCount -ne $Repeats) {
  throw "Real-model result repeat_count does not match the requested repeats."
}

$responseModelMatchRate = Get-RequiredSection `
  -InputObject $rawModel `
  -Name "response_model_match_rate" `
  -Context "raw_model"
$responseModelMatchNumerator = ConvertTo-NonNegativeInteger `
  -Value (Get-RequiredProperty `
    -InputObject $responseModelMatchRate `
    -Name "numerator" `
    -Context "response_model_match_rate") `
  -Name "response_model_match_rate.numerator"
$responseModelMatchDenominator = ConvertTo-NonNegativeInteger `
  -Value (Get-RequiredProperty `
    -InputObject $responseModelMatchRate `
    -Name "denominator" `
    -Context "response_model_match_rate") `
  -Name "response_model_match_rate.denominator"
if ($responseModelMatchDenominator -le 0) {
  throw "Raw-model response-model match denominator must be greater than zero."
}
if ($responseModelMatchNumerator -ne $responseModelMatchDenominator) {
  throw "Raw-model response-model match numerator must equal its denominator."
}
$responseModelMatchRateValue = ConvertTo-FiniteNumber `
  -Value (Get-RequiredProperty `
    -InputObject $responseModelMatchRate `
    -Name "rate" `
    -Context "response_model_match_rate") `
  -Name "response_model_match_rate.rate"
$recalculatedResponseModelMatchRate = (
  [double]$responseModelMatchNumerator / [double]$responseModelMatchDenominator
)
if (
  $responseModelMatchRateValue -ne $recalculatedResponseModelMatchRate -or
  $responseModelMatchRateValue -ne 1.0
) {
  throw "Raw-model response-model match rate must be exactly recomputable and equal 1.0."
}

$safetyMetrics = @(
  "persisted_unknown_citation_count",
  "unsupported_numeric_claim_count",
  "redacted_reference_count"
)
foreach ($metric in $safetyMetrics) {
  $metricValue = ConvertTo-NonNegativeInteger `
    -Value (Get-RequiredProperty `
      -InputObject $validatedSystem `
      -Name $metric `
      -Context "validated_system") `
    -Name $metric
  if ($metricValue -ne 0) {
    throw "Validated-system safety gate failed: $metric must be zero."
  }
}

$jobCount = ConvertTo-NonNegativeInteger `
  -Value (Get-RequiredProperty `
    -InputObject $validatedSystem `
    -Name "job_count" `
    -Context "validated_system") `
  -Name "job_count"
if ($jobCount -le 0) {
  throw "Validated-system job denominator must be greater than zero."
}
if ($jobCount -ne ($caseCount * $repeatCount)) {
  throw "Validated-system job denominator does not match cases and repeats."
}

$effectiveRate = Get-RequiredSection `
  -InputObject $validatedSystem `
  -Name "effective_output_rate" `
  -Context "validated_system"
$effectiveOutputCount = ConvertTo-NonNegativeInteger `
  -Value (Get-RequiredProperty `
    -InputObject $effectiveRate `
    -Name "numerator" `
    -Context "effective_output_rate") `
  -Name "effective_output_rate.numerator"
$effectiveDenominator = ConvertTo-NonNegativeInteger `
  -Value (Get-RequiredProperty `
    -InputObject $effectiveRate `
    -Name "denominator" `
    -Context "effective_output_rate") `
  -Name "effective_output_rate.denominator"
if ($effectiveDenominator -le 0) {
  throw "Validated-system effective-output denominator must be greater than zero."
}
if ($effectiveOutputCount -le 0) {
  throw "Validated-system effective-output numerator must be greater than zero."
}
if (
  $effectiveDenominator -ne $jobCount -or
  $effectiveOutputCount -gt $effectiveDenominator
) {
  throw "Validated-system effective-output ratio is inconsistent."
}

$expectedMatchRate = Get-RequiredSection `
  -InputObject $validatedSystem `
  -Name "expected_status_match_rate" `
  -Context "validated_system"
$expectedMatchNumerator = ConvertTo-NonNegativeInteger `
  -Value (Get-RequiredProperty `
    -InputObject $expectedMatchRate `
    -Name "numerator" `
    -Context "expected_status_match_rate") `
  -Name "expected_status_match_rate.numerator"
$expectedMatchDenominator = ConvertTo-NonNegativeInteger `
  -Value (Get-RequiredProperty `
    -InputObject $expectedMatchRate `
    -Name "denominator" `
    -Context "expected_status_match_rate") `
  -Name "expected_status_match_rate.denominator"
if ($expectedMatchDenominator -ne $jobCount) {
  throw "Expected-status match denominator must equal job_count."
}
if ($expectedMatchNumerator -gt $expectedMatchDenominator) {
  throw "Expected-status match numerator cannot exceed its denominator."
}
$expectedMatchRateValue = ConvertTo-FiniteNumber `
  -Value (Get-RequiredProperty `
    -InputObject $expectedMatchRate `
    -Name "rate" `
    -Context "expected_status_match_rate") `
  -Name "expected_status_match_rate.rate"
$recalculatedMatchRate = [Math]::Round(
  [double]$expectedMatchNumerator / [double]$expectedMatchDenominator,
  3,
  [System.MidpointRounding]::ToEven
)
if ([Math]::Abs($expectedMatchRateValue - $recalculatedMatchRate) -gt 0.000000001) {
  throw "Expected-status match rate is inconsistent with its counts."
}
if ($expectedMatchRateValue -lt 0.8) {
  throw "Expected-status match rate must be at least 0.8."
}

$terminalDistribution = Get-RequiredSection `
  -InputObject $validatedSystem `
  -Name "terminal_distribution" `
  -Context "validated_system"
$terminalCount = 0
$terminalCounts = @{}
foreach ($terminalStatus in @("actionable", "needs_evidence", "refused", "failed")) {
  $statusCount = ConvertTo-NonNegativeInteger `
    -Value (Get-RequiredProperty `
      -InputObject $terminalDistribution `
      -Name $terminalStatus `
      -Context "terminal_distribution") `
    -Name "terminal_distribution.$terminalStatus"
  $terminalCounts[$terminalStatus] = $statusCount
  $terminalCount += $statusCount
}
if ($terminalCount -ne $jobCount) {
  throw "Validated-system terminal denominator is inconsistent."
}
$terminalEffectiveCount = (
  $terminalCounts["actionable"] + $terminalCounts["needs_evidence"]
)
if ($effectiveOutputCount -ne $terminalEffectiveCount) {
  throw "Validated-system effective-output count is inconsistent with terminals."
}

$providerAttemptCount = ConvertTo-NonNegativeInteger `
  -Value (Get-RequiredProperty `
    -InputObject $runtime `
    -Name "provider_attempt_count" `
    -Context "runtime") `
  -Name "provider_attempt_count"

$safeSummary = [ordered]@{
  status = "passed"
  model = $artifactTag
  digest = $digest
  case_count = $caseCount
  repeat_count = $repeatCount
  job_count = $jobCount
  effective_output_count = $effectiveOutputCount
  provider_attempt_count = $providerAttemptCount
}
Write-Output ($safeSummary | ConvertTo-Json -Compress)
