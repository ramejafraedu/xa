param(
    [string]$Server = "34.41.250.143",
    [string]$User = "ramej",
    [string]$Key = "id_rsa_ramej",
    [string]$RemoteDir = "video_factory",
    [int]$Port = 22
)

function Read-DotEnvMap {
    param([string]$Path)
    $map = @{}
    if (-not (Test-Path -LiteralPath $Path)) { return $map }
    foreach ($line in Get-Content -LiteralPath $Path -Encoding UTF8) {
        $t = $line.Trim()
        if (-not $t -or $t.StartsWith("#")) { continue }
        $eq = $t.IndexOf("=")
        if ($eq -lt 1) { continue }
        $k = $t.Substring(0, $eq).Trim()
        $v = $t.Substring($eq + 1).Trim()
        if (
            ($v.Length -ge 2) -and (
                ($v.StartsWith('"') -and $v.EndsWith('"')) -or
                ($v.StartsWith("'") -and $v.EndsWith("'"))
            )
        ) {
            $v = $v.Substring(1, $v.Length - 2)
        }
        $map[$k] = $v
    }
    return $map
}

$envPath = Join-Path $PSScriptRoot ".env"
$dot = Read-DotEnvMap $envPath

if (-not $PSBoundParameters.ContainsKey("Server") -and $dot["SSH_HOST"]) {
    $Server = $dot["SSH_HOST"].Trim()
}
if (-not $PSBoundParameters.ContainsKey("User") -and $dot["SSH_USER"]) {
    $User = $dot["SSH_USER"].Trim()
}
if (-not $PSBoundParameters.ContainsKey("Key") -and $dot["SSH_PRIVATE_KEY"]) {
    $Key = $dot["SSH_PRIVATE_KEY"].Trim()
}
if (-not $PSBoundParameters.ContainsKey("Port") -and $dot["SSH_PORT"]) {
    $p = $dot["SSH_PORT"].Trim()
    if ($p -match "^\d+$") { $Port = [int]$p }
}

$Key = ($Key -replace "^\./", "").Trim()
$keyPath = if ([System.IO.Path]::IsPathRooted($Key)) { $Key } else { Join-Path $PSScriptRoot $Key }
$zipFile = Join-Path $PSScriptRoot "deploy_video_factory.zip"

$sshTarget = "${User}@${Server}"
$sshBase = @("-i", $keyPath, "-o", "StrictHostKeyChecking=no")
$scpBase = @("-i", $keyPath, "-o", "StrictHostKeyChecking=no")
if ($Port -ne 22) {
    $sshBase = @("-p", "$Port") + $sshBase
    $scpBase = @("-P", "$Port") + $scpBase
}

Write-Host "Empaquetando el proyecto..."
$exclude = @("venv", ".venv", "node_modules", ".git", "deploy_video_factory.zip", "__pycache__", ".vscode", ".gemini")

$tempDir = Join-Path $env:TEMP "video_factory_deploy"
if (Test-Path $tempDir) { Remove-Item -Recurse -Force $tempDir }
New-Item -ItemType Directory -Path $tempDir | Out-Null

Write-Host "Copiando archivos..."
Push-Location $PSScriptRoot
try {
    robocopy . $tempDir /MIR /XD $exclude /XF "deploy_video_factory.zip" "*.pyc" "id_rsa_ramej" "id_rsa_ramej.pub" "google_credentials.json" "gcp_credentials.json" "inductive-actor*.json" "*service-account*.json" /NFL /NDL /NJH /NJS /nc /ns /np
} finally {
    Pop-Location
}

Write-Host "Comprimiendo..."
Compress-Archive -Path "$tempDir\*" -DestinationPath $zipFile -Force

Write-Host "Subiendo a ${sshTarget} (puerto $Port)..."
& scp @scpBase $zipFile "${sshTarget}:~/"

$remoteUnzip = "unzip -o -q deploy_video_factory.zip -d ~/$RemoteDir && rm deploy_video_factory.zip"
Write-Host "Descomprimiendo en el servidor remoto (~/$RemoteDir)..."
& ssh @sshBase $sshTarget $remoteUnzip

Write-Host "Limpiando local..."
Remove-Item $zipFile -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force $tempDir

Write-Host "Despliegue completado. Codigo en ~/$RemoteDir en $Server"
