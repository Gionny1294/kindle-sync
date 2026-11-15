param([switch]$Lan, [switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
try {
    Set-Location (Split-Path -Parent $PSScriptRoot)
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        throw 'Install and start Docker Desktop, then run Start.cmd again. See README.md.'
    }
    & docker compose version *> $null
    if ($LASTEXITCODE -ne 0) { throw 'Docker Compose v2 is required. Update Docker Desktop.' }
    & docker info *> $null
    if ($LASTEXITCODE -ne 0) { throw 'Docker is not ready. Start Docker Desktop with Linux containers and try again.' }
    New-Item -ItemType Directory -Force -Path data, library | Out-Null
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    if (-not (Test-Path -LiteralPath .env)) {
        $bindIP = '127.0.0.1'
        if ($Lan) { $bindIP = '0.0.0.0' }
        [System.IO.File]::WriteAllText((Join-Path $PWD '.env'), "APP_BIND_IP=$bindIP`nAPP_PORT=1997`nAPP_UID=1000`nAPP_GID=1000`nLIBRARY_HOST_PATH=./library`nCOOKIE_SECURE=false`n", $utf8)
    } elseif ($Lan) {
        $lines = @(Get-Content -LiteralPath .env | Where-Object { $_ -notmatch '^APP_BIND_IP=' })
        $lines += 'APP_BIND_IP=0.0.0.0'
        [System.IO.File]::WriteAllText((Join-Path $PWD '.env'), ($lines -join "`n") + "`n", $utf8)
    }
    Write-Host 'Starting Kindle Sync. The first build downloads dependencies and may take several minutes.'
    & docker compose up -d --build --wait --wait-timeout 240
    if ($LASTEXITCODE -ne 0) {
        throw 'Startup failed. Check Docker, the internet connection and port conflicts. Run: docker compose logs --tail=50 app worker. Existing data has been kept.'
    }
    $port = '1997'
    foreach ($line in (Get-Content -LiteralPath .env)) {
        if ($line -match '^APP_PORT=(\d+)$') { $port = $Matches[1]; break }
    }
    $url = "http://localhost:$port"
    Write-Host "Kindle Sync is ready: $url"
    Write-Host 'Keep Docker Desktop running while using the app.'
    if ($Lan) { Write-Host "Local network access enabled. On another device use http://YOUR-COMPUTER-IP:$port on a trusted network." }
    if (-not $NoBrowser) { Start-Process $url }
    exit 0
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
