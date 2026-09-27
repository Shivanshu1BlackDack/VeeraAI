# Smoke tests — requires bot running on http://localhost:8080
$Base = $env:BOT_URL
if (-not $Base) { $Base = "http://localhost:8080" }

Write-Host "GET /v1/healthz"
curl.exe -sS "$Base/v1/healthz"

Write-Host "`nGET /v1/metadata"
curl.exe -sS "$Base/v1/metadata"

Write-Host "`nPOST /v1/context (category dentists)"
$cat = Get-Content -Raw "$PSScriptRoot\..\dataset\categories\dentists.json"
$body = @{
  scope = "category"
  context_id = "dentists"
  version = 1
  payload = (ConvertFrom-Json $cat)
  delivered_at = "2026-04-26T10:00:00Z"
} | ConvertTo-Json -Depth 30
curl.exe -sS -X POST "$Base/v1/context" -H "Content-Type: application/json" -d $body

Write-Host "`nDone."
