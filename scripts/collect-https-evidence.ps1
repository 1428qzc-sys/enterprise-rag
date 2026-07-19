<#
.SYNOPSIS
    按 DEPLOYMENT.md §8 采集公网 HTTPS 验收证据到 docs/evidence/

.PARAMETER Domain
    公网域名，如 rag-staging.example.com

.PARAMETER AdminEmail
    登录邮箱（默认 admin@example.com）

.PARAMETER AdminPassword
    登录密码（勿写入日志；默认 ChangeMe123!）

.EXAMPLE
    .\scripts\collect-https-evidence.ps1 -Domain rag-staging.example.com
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Domain,

    [string]$AdminEmail = 'admin@example.com',

    [string]$AdminPassword = 'ChangeMe123!'
)

$ErrorActionPreference = 'Stop'
$Date = Get-Date -Format 'yyyyMMdd'
$EvidenceDir = Join-Path (Split-Path $PSScriptRoot -Parent) 'docs\evidence'
New-Item -ItemType Directory -Force -Path $EvidenceDir | Out-Null

$Base = "https://$Domain"
Write-Host "=== Enterprise RAG · HTTPS Evidence Collection ===" -ForegroundColor Cyan
Write-Host "Domain: $Domain · Date: $Date" -ForegroundColor Cyan

# §8.1 DNS
$dnsFile = Join-Path $EvidenceDir "dns-$Domain-$Date.txt"
try {
    nslookup $Domain 2>&1 | Out-File -FilePath $dnsFile -Encoding utf8
    Write-Host "[OK] DNS -> $dnsFile" -ForegroundColor Green
} catch {
    Write-Warning "DNS lookup failed: $_"
}

# §8.2 TLS
$tlsFile = Join-Path $EvidenceDir "tls-$Domain-$Date.txt"
curl.exe -vI "$Base/" 2>&1 | Out-File -FilePath $tlsFile -Encoding utf8
Write-Host "[OK] TLS verbose -> $tlsFile" -ForegroundColor Green

# §8.3 Health
$healthFile = Join-Path $EvidenceDir "health-$Domain-$Date.json"
curl.exe -fsS "$Base/api/health" | Out-File -FilePath $healthFile -Encoding utf8
Write-Host "[OK] Health -> $healthFile" -ForegroundColor Green

# §8.3 Login (redacted output)
$loginFile = Join-Path $EvidenceDir "login-smoke-$Domain-$Date.txt"
$loginBody = @{ email = $AdminEmail; password = $AdminPassword } | ConvertTo-Json -Compress
try {
    $resp = Invoke-WebRequest -Uri "$Base/api/auth/login" -Method POST -Body $loginBody -ContentType 'application/json' -UseBasicParsing
    $hasToken = ($resp.Content -match 'access_token')
    "HTTP $($resp.StatusCode) access_token_present=$hasToken (token body redacted)" | Out-File -FilePath $loginFile -Encoding utf8
    Write-Host "[OK] Login smoke -> $loginFile" -ForegroundColor Green
} catch {
    "LOGIN FAILED: $($_.Exception.Message)" | Out-File -FilePath $loginFile -Encoding utf8
    Write-Warning "Login smoke failed — see $loginFile"
}

Write-Host ""
Write-Host "Next steps:" -ForegroundColor Yellow
Write-Host "  1. Optional SSL Labs screenshot -> docs/evidence/ssl-labs-$Domain-$Date.png"
Write-Host "  2. Run: python backend/scripts/smoke_auth_flow.py --base-url $Base"
Write-Host "  3. Copy acceptance-checklist.example.md and fill items 1-12"
Write-Host "  4. Do NOT commit real tokens to git"
