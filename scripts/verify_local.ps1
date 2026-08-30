param(
  [string]$ApiBaseUrl = "http://localhost:8000",
  [string]$WebBaseUrl = "http://localhost:3000",
  [string]$ResultsPath = (Join-Path $PSScriptRoot "..\eval\results.json"),
  [string]$EvalScriptPath = (Join-Path $PSScriptRoot "..\eval\run_eval.py"),
  [string]$GoldReviewsPath = (Join-Path $PSScriptRoot "..\eval\gold_reviews.jsonl"),
  [string]$GoldDecisionMemosPath = (Join-Path $PSScriptRoot "..\eval\gold_decision_memos.jsonl"),
  [ValidateRange(1, 300)]
  [int]$RequestTimeoutSeconds = 10,
  [switch]$ExpectedDemoReadOnly
)

$health = Invoke-RestMethod "$ApiBaseUrl/healthz" `
  -TimeoutSec $RequestTimeoutSeconds
if ($health.status -ne "ok" -or $health.database -ne "ready") {
  throw "API health check failed."
}

$webResponse = Invoke-WebRequest -UseBasicParsing $WebBaseUrl `
  -TimeoutSec $RequestTimeoutSeconds
if ($webResponse.StatusCode -lt 200 -or $webResponse.StatusCode -ge 400) {
  throw "Web application did not return a successful response."
}

$modelHealth = Invoke-RestMethod "$ApiBaseUrl/healthz/model" `
  -TimeoutSec $RequestTimeoutSeconds
$expectedModelFields = @("configured", "ready", "provider", "model_name", "error_code") |
  Sort-Object
$actualModelFields = @($modelHealth.PSObject.Properties.Name) | Sort-Object
$modelFieldDifference = Compare-Object `
  -ReferenceObject $expectedModelFields `
  -DifferenceObject $actualModelFields
if ($null -ne $modelFieldDifference) {
  throw "Model health response has an unexpected shape."
}

if ($ExpectedDemoReadOnly) {
  $overview = Invoke-RestMethod "$ApiBaseUrl/api/v1/overview" `
    -TimeoutSec $RequestTimeoutSeconds
  $datasetVersionId = [string]$overview.active_dataset.id
  if ([string]::IsNullOrWhiteSpace($datasetVersionId)) {
    throw "Read-only gate could not resolve the active dataset version."
  }
  $requestBody = @{ dataset_version_id = $datasetVersionId } |
    ConvertTo-Json -Compress
  $request = [System.Net.HttpWebRequest]::Create("$ApiBaseUrl/api/v1/decision-memo/generate")
  $request.Method = "POST"
  $request.ContentType = "application/json"
  $request.Timeout = $RequestTimeoutSeconds * 1000
  $requestBytes = [System.Text.Encoding]::UTF8.GetBytes($requestBody)
  $request.ContentLength = $requestBytes.Length
  $requestStream = $request.GetRequestStream()
  try {
    $requestStream.Write($requestBytes, 0, $requestBytes.Length)
  }
  finally {
    $requestStream.Dispose()
  }
  $readOnlyResponse = $null
  try {
    $readOnlyResponse = $request.GetResponse()
  }
  catch [System.Net.WebException] {
    $readOnlyResponse = $_.Exception.Response
  }
  if ($null -eq $readOnlyResponse -or [int]$readOnlyResponse.StatusCode -ne 403) {
    throw "Expected read-only demo generation to return HTTP 403."
  }
  $reader = New-Object System.IO.StreamReader($readOnlyResponse.GetResponseStream())
  try {
    $errorPayload = $reader.ReadToEnd() | ConvertFrom-Json -ErrorAction Stop
  }
  finally {
    $reader.Dispose()
    $readOnlyResponse.Dispose()
  }
  if ($errorPayload.detail.code -ne "DEMO_READ_ONLY") {
    throw "Read-only demo generation returned an unexpected error code."
  }
}

if (-not (Test-Path -LiteralPath $ResultsPath)) {
  throw "Evaluation results are missing. Run .\.venv\Scripts\python.exe eval\run_eval.py first."
}
$evaluationSources = @(
  $EvalScriptPath,
  $GoldReviewsPath,
  $GoldDecisionMemosPath
)
foreach ($source in $evaluationSources) {
  if (-not (Test-Path -LiteralPath $source)) {
    throw "Evaluation source is missing: $source"
  }
}
$resultsTimestamp = (Get-Item -LiteralPath $ResultsPath).LastWriteTimeUtc
$newestSource = $evaluationSources |
  ForEach-Object { Get-Item -LiteralPath $_ } |
  Sort-Object LastWriteTimeUtc -Descending |
  Select-Object -First 1
if ($resultsTimestamp -lt $newestSource.LastWriteTimeUtc) {
  throw "Evaluation results are stale. Run .\.venv\Scripts\python.exe eval\run_eval.py again."
}
try {
  $evaluation = Get-Content -Raw -Encoding UTF8 -LiteralPath $ResultsPath |
    ConvertFrom-Json -ErrorAction Stop
}
catch {
  throw "Evaluation results are not valid UTF-8 JSON. Run .\.venv\Scripts\python.exe eval\run_eval.py first."
}
$requiredMetrics = @(
  "subproblem_evidence_precision",
  "citation_completeness",
  "unsupported_numeric_rate",
  "decision_status_accuracy",
  "evidence_plan_specificity"
)
foreach ($metric in $requiredMetrics) {
  $property = $evaluation.PSObject.Properties[$metric]
  if ($null -eq $property) {
    throw "Evaluation result is missing $metric. Run .\.venv\Scripts\python.exe eval\run_eval.py first."
  }
  try {
    $value = [double]$property.Value
  }
  catch {
    throw "Decision memo regression gate failed: $metric must be numeric."
  }
  if ([double]::IsNaN($value) -or [double]::IsInfinity($value) -or $value -lt 0 -or $value -gt 1) {
    throw "Decision memo regression gate failed: $metric must be between zero and one."
  }
}
$minimumGates = [ordered]@{
  subproblem_evidence_precision = 0.75
  citation_completeness = 0.85
  decision_status_accuracy = 0.80
  evidence_plan_specificity = 0.75
}
foreach ($metric in $minimumGates.Keys) {
  $actual = [double]$evaluation.PSObject.Properties[$metric].Value
  $minimum = [double]$minimumGates[$metric]
  if ($actual -lt $minimum) {
    throw "Decision memo regression gate failed: $metric must be at least $minimum."
  }
}
if ([double]$evaluation.unsupported_numeric_rate -ne 0) {
  throw "Decision memo regression gate failed: unsupported_numeric_rate must be zero."
}

$readOnlyMessage = if ($ExpectedDemoReadOnly) { " read-only demo gate passed." } else { "" }
Write-Output "Local verification passed: API healthy, web reachable, fresh evaluation gates passed, and model health shape valid.$readOnlyMessage"
