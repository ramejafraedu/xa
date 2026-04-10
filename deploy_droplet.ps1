# DigitalOcean Droplet (u otro Ubuntu con SSH).
# Ejemplos:
#   .\deploy_droplet.ps1 -Server 203.0.113.50
#   .\deploy_droplet.ps1 -Server 203.0.113.50 -User root -Key id_rsa_ramej
#   $env:VIDEO_FACTORY_DROPLET_IP = "203.0.113.50"; .\deploy_droplet.ps1

param(
    [string]$Server = $env:VIDEO_FACTORY_DROPLET_IP,
    [string]$User = $(if ($env:VIDEO_FACTORY_DROPLET_USER) { $env:VIDEO_FACTORY_DROPLET_USER } else { "root" }),
    [string]$Key = $(if ($env:VIDEO_FACTORY_DROPLET_KEY) { $env:VIDEO_FACTORY_DROPLET_KEY } else { "id_rsa_ramej" }),
    [string]$RemoteDir = "video_factory"
)

if ([string]::IsNullOrWhiteSpace($Server)) {
    $envPath = Join-Path $PSScriptRoot ".env"
    if (Test-Path -LiteralPath $envPath) {
        foreach ($line in Get-Content -LiteralPath $envPath -Encoding UTF8) {
            $t = $line.Trim()
            if ($t -match '^\s*SSH_HOST\s*=\s*(.+)$') {
                $Server = $matches[1].Trim().Trim('"').Trim("'")
                break
            }
        }
    }
}

if ([string]::IsNullOrWhiteSpace($Server)) {
    Write-Host "Falta la IP o hostname del Droplet." -ForegroundColor Red
    Write-Host "  .\deploy_droplet.ps1 -Server <IP_PUBLICA> [-User root] [-Key id_rsa_ramej]" -ForegroundColor Yellow
    Write-Host "  o: `$env:VIDEO_FACTORY_DROPLET_IP = '<IP>'  o  SSH_HOST en .env" -ForegroundColor Yellow
    exit 1
}

$deploy = Join-Path $PSScriptRoot "deploy_video_factory.ps1"
& $deploy -Server $Server -User $User -Key $Key -RemoteDir $RemoteDir
