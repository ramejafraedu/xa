# Deploy Video Factory V16.1 to Ubuntu Server (Windows Native)
# SSH_HOST=35.239.64.169, SSH_USER=xavierfranmen

$SSH_HOST = $env:SSH_HOST
if ([string]::IsNullOrEmpty($SSH_HOST)) { $SSH_HOST = "34.173.161.219" }

$SSH_USER = $env:SSH_USER
if ([string]::IsNullOrEmpty($SSH_USER)) { $SSH_USER = "xavierfranmen" }

$sshKeyEnv = $env:SSH_PRIVATE_KEY
if (-not [string]::IsNullOrEmpty($sshKeyEnv)) {
    if ($sshKeyEnv.StartsWith('./') -or $sshKeyEnv.StartsWith('.\')) {
        $SSH_KEY = Join-Path $PSScriptRoot $sshKeyEnv.Substring(2)
    }
    elseif ([System.IO.Path]::IsPathRooted($sshKeyEnv)) {
        $SSH_KEY = $sshKeyEnv
    }
    else {
        $SSH_KEY = Join-Path $PSScriptRoot $sshKeyEnv
    }
}
else {
    $SSH_KEY = Join-Path $PSScriptRoot 'id_ed25519_xavito'
}
$REMOTE_DIR = "/home/xavierfranmen/video_factory"
$TEMP_DIR = "$env:TEMP"
$deployStamp = [guid]::NewGuid().ToString("n").Substring(0, 10)

Write-Host "🚀 Desplegando Video Factory V16.1 a $SSH_HOST" -ForegroundColor Green
Write-Host ""

# Test SSH
Write-Host "🔍 Verificando conexion SSH..." -ForegroundColor Yellow
try {
    $test = ssh -i $SSH_KEY -o StrictHostKeyChecking=no -o ConnectTimeout=10 "$SSH_USER@$SSH_HOST" "echo SSH_OK" 2>&1
    if ($test -match "SSH_OK") {
        Write-Host "✅ Conexion SSH exitosa" -ForegroundColor Green
    }
    else {
        Write-Host "❌ Error SSH: $test" -ForegroundColor Red
        exit 1
    }
}
catch {
    Write-Host "❌ Error: $_" -ForegroundColor Red
    exit 1
}

# Crear ZIP excluyendo carpetas innecesarias
Write-Host "📦 Comprimiendo archivos..." -ForegroundColor Yellow
$sourceDir = $PSScriptRoot
# Nombre unico evita choque si otro deploy o tar.exe aun tiene el archivo abierto
$zipFile = Join-Path $TEMP_DIR "vf_deploy_$deployStamp.tar.gz"

# Crear lista de exclusiones para tar
$excludeFile = "$TEMP_DIR\exclude.txt"
@(
    '.git', '.venv', '__pycache__', '*.pyc', '*.pyo', '*.pyd',
    '.pytest_cache', '*.egg-info', 'dist', 'build', '.windsurf',
    'OpenMontage-main', '*.tar.gz', '*.tgz', '*.zip',
    'logs', 'temp', 'outputs', 'workspace'
) | Set-Content $excludeFile

# Comprimir usando tar (más rápido y soporta exclusiones reales en Windows 10/11)
tar.exe -czf $zipFile -X $excludeFile -C $sourceDir .

# Verificar
if (-not (Test-Path $zipFile)) {
    Write-Host "❌ Error creando ZIP" -ForegroundColor Red
    exit 1
}

$size = (Get-Item $zipFile).Length / 1MB
Write-Host "✅ TAR creado: $([math]::Round($size,2)) MB" -ForegroundColor Green

# Subir al servidor
Write-Host "⬆️  Subiendo al servidor..." -ForegroundColor Yellow
scp -i $SSH_KEY -o StrictHostKeyChecking=no $zipFile "${SSH_USER}@${SSH_HOST}:/tmp/vf_deploy_incoming.tar.gz"

if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ Error subiendo archivos" -ForegroundColor Red
    exit 1
}

# Extraer y configurar en servidor
Write-Host "📂 Configurando en servidor..." -ForegroundColor Yellow
$remoteCommands = "
set -e
mkdir -p $REMOTE_DIR
cd $REMOTE_DIR

# Backup si existe
if [ -f video_factory.py ]; then
    mv config.py config.py.backup 2>/dev/null || true
fi

# Extraer nuevo codigo
tar -xzf /tmp/vf_deploy_incoming.tar.gz -C $REMOTE_DIR
rm -f /tmp/vf_deploy_incoming.tar.gz

# Restaurar .env si existe backup
if [ -f $REMOTE_DIR/../.env ]; then
    cp $REMOTE_DIR/../.env $REMOTE_DIR/.env
fi

# Permisos
chmod +x setup_ubuntu.sh 2>/dev/null || true

echo '✅ Codigo desplegado en $REMOTE_DIR'
echo '🎬 Version: V16.1 - Anti-Repeticion + Remotion'
"

$remoteCommands = $remoteCommands -replace "`r`n", "`n" -replace "`r", "`n"

ssh -i $SSH_KEY -o StrictHostKeyChecking=no "$SSH_USER@$SSH_HOST" $remoteCommands

# Limpiar local
Remove-Item $zipFile -Force

Write-Host ""
Write-Host "✅ DESPLIEGUE COMPLETADO!" -ForegroundColor Green
Write-Host "📍 Ubicacion: $REMOTE_DIR"
Write-Host "🎬 Para ejecutar: ssh $SSH_USER@$SSH_HOST 'cd $REMOTE_DIR && python video_factory.py --test curiosidades'"
