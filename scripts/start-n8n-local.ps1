param(
    [int]$Port = 5678,
    [string]$BindHost = 'localhost'
)

$ErrorActionPreference = 'Stop'

$editorBaseUrl = "http://$BindHost`:$Port"
$webhookUrl = "$editorBaseUrl/"

$env:N8N_HOST = $BindHost
$env:N8N_PORT = "$Port"
$env:N8N_PROTOCOL = 'http'
$env:N8N_EDITOR_BASE_URL = $editorBaseUrl
$env:WEBHOOK_URL = $webhookUrl

Write-Host "Starting n8n locally on $editorBaseUrl"
Write-Host "Tip: In this non-Docker setup, your backend base URL should be http://localhost:8001"
Write-Host "Press Ctrl+C to stop."

if (Get-Command n8n -ErrorAction SilentlyContinue) {
    n8n
}
else {
    Write-Host 'Global n8n command not found.'
    Write-Host 'npx fallback is disabled because npm exec is failing with "Invalid Version" on this machine.'
    Write-Host 'Run once:'
    Write-Host '  npm install -g n8n'
    Write-Host 'Then run this script again.'
    exit 1
}
