param(
  [string]$ApiBaseUrl = "http://localhost:8000",
  [ValidateRange(1, 300)]
  [int]$RequestTimeoutSeconds = 10
)

$health = Invoke-RestMethod "$ApiBaseUrl/healthz/model" `
  -TimeoutSec $RequestTimeoutSeconds
$expectedFields = @("configured", "ready", "provider", "model_name", "error_code") | Sort-Object
$actualFields = @($health.PSObject.Properties.Name) | Sort-Object
$fieldDifference = Compare-Object -ReferenceObject $expectedFields -DifferenceObject $actualFields
if ($null -ne $fieldDifference) {
  throw "Model health response has an unexpected shape."
}

$safeHealth = [ordered]@{
  configured = [bool]$health.configured
  ready = [bool]$health.ready
  provider = $health.provider
  model_name = $health.model_name
  error_code = $health.error_code
}
Write-Output ($safeHealth | ConvertTo-Json -Compress)

if ($health.ready) {
  Write-Output "Local model verification passed."
  return
}

if ((-not $health.configured) -or $health.error_code -eq "LOCAL_MODEL_UNAVAILABLE") {
  Write-Output "Local model is unavailable or unconfigured; this is a non-fatal development state."
  return
}

throw "Configured local model is not ready: $($health.error_code)"
