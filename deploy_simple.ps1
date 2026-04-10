# Deploy Video Factory V16.1 to Ubuntu Server
# SSH_HOST=34.41.250.143, SSH_USER=ramej

$SSH_HOST = "34.41.250.143"
$SSH_USER = "ramej"
$SSH_KEY = ".\id_rsa_ramej"
$REMOTE_DIR = "/home/ramej/video_factory"

Write-Host "🚀 Desplegando Video Factory V16.1 a $SSH_HOST" -ForegroundColor Green
Write-Host ""

# Test SSH connection
Write-Host "🔍 Verificando conexion SSH..." -ForegroundColor Yellow
try {
    $testResult = ssh -i $SSH_KEY -o StrictHostKeyChecking=no -o ConnectTimeout=10 "$SSH_USER@$SSH_HOST" "echo 'SSH_OK'" 2>&1
    if ($testResult -match "SSH_OK") {
        Write-Host "✅ Conexion SSH exitosa" -ForegroundColor Green
    } else {
        Write-Host "❌ Error de conexion SSH: $testResult" -ForegroundColor Red
        exit 1
    }
} catch {
    Write-Host "❌ Error: $_" -ForegroundColor Red
    exit 1
}

# Create tar.gz excluding unnecessary files
Write-Host "📦 Preparando archivos..." -ForegroundColor Yellow
$excludePatterns = @(
    '.git', '.venv', '__pycache__', '*.pyc', '*.pyo', '*.pyd',
    '.pytest_cache', '*.egg-info', 'dist', 'build', '.windsurf',
    'OpenMontage-main', '*.tar.gz', '*.tgz', 'logs/*.log',
    'temp/*', 'outputs/*', 'remotion-composer/node_modules', 'workspace/*'
)

# Create tar command
$tarArgs = "czf /tmp/vf_deploy.tar.gz"
foreach ($pattern in $excludePatterns) {
    $tarArgs += " --exclude='$pattern'"
}
$tarArgs += " ."

bash -c "cd 'c:/Users/ramej/OneDrive/Escritorio/Nueva carpeta/video_factory' && $tarArgs"

# Upload
Write-Host "⬆️  Subiendo archivos..." -ForegroundColor Yellow
scp -i $SSH_KEY -o StrictHostKeyChecking=no /tmp/vf_deploy.tar.gz "${SSH_USER}@${SSH_HOST}:/tmp/"

# Extract and setup on server
Write-Host "📂 Configurando en servidor..." -ForegroundColor Yellow
ssh -i $SSH_KEY -o StrictHostKeyChecking=no "$SSH_USER@$SSH_HOST" @"
    set -e
    mkdir -p $REMOTE_DIR
    cd $REMOTE_DIR
    tar xzf /tmp/vf_deploy.tar.gz
    rm /tmp/vf_deploy.tar.gz
    chmod +x setup_ubuntu.sh
    echo '✅ Archivos desplegados'
    echo '📍 Ubicacion: $REMOTE_DIR'
    echo '🔧 Ejecuta manualmente: cd $REMOTE_DIR && ./setup_ubuntu.sh'
"@

Write-Host ""
Write-Host "✅ Despliegue completado!" -ForegroundColor Green
Write-Host "📍 Ubicacion: $REMOTE_DIR"
Write-Host "🔧 Ejecuta en servidor: cd $REMOTE_DIR && ./setup_ubuntu.sh"
