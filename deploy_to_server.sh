#!/bin/bash
# Deploy Video Factory V16.1 to Ubuntu Server
# SSH_HOST=34.41.250.143
# SSH_USER=ramej
# SSH_KEY=./id_rsa_ramej

set -e

SSH_HOST="34.41.250.143"
SSH_USER="ramej"
SSH_KEY="./id_rsa_ramej"
REMOTE_DIR="/home/ramej/video_factory"

echo "🚀 Desplegando Video Factory V16.1 a $SSH_HOST"
echo ""

# 1. Verificar conexion SSH
echo "🔍 Verificando conexion SSH..."
ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no -o ConnectTimeout=10 "$SSH_USER@$SSH_HOST" "echo '✅ SSH OK'" || {
    echo "❌ Error de conexion SSH"
    exit 1
}

# 2. Crear directorio remoto
echo "📁 Creando directorio remoto..."
ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$SSH_USER@$SSH_HOST" "mkdir -p $REMOTE_DIR"

# 3. Excluir archivos innecesarios y comprimir
echo "📦 Preparando archivos..."
tar czf /tmp/video_factory_deploy.tar.gz \
    --exclude='.git' \
    --exclude='.venv' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='*.pyo' \
    --exclude='*.pyd' \
    --exclude='.pytest_cache' \
    --exclude='*.egg-info' \
    --exclude='dist' \
    --exclude='build' \
    --exclude='.windsurf' \
    --exclude='OpenMontage-main' \
    --exclude='*.tar.gz' \
    --exclude='*.tgz' \
    --exclude='logs/*.log' \
    --exclude='temp/*' \
    --exclude='outputs/*' \
    --exclude='remotion-composer/node_modules' \
    --exclude='workspace/*' \
    .

# 4. Subir archivos
echo "⬆️  Subiendo archivos al servidor..."
scp -i "$SSH_KEY" -o StrictHostKeyChecking=no /tmp/video_factory_deploy.tar.gz "$SSH_USER@$SSH_HOST:/tmp/"

# 5. Descomprimir en servidor
echo "📂 Extrayendo archivos..."
ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$SSH_USER@$SSH_HOST" "
    cd $REMOTE_DIR
    tar xzf /tmp/video_factory_deploy.tar.gz
    rm /tmp/video_factory_deploy.tar.gz
"

# 6. Ejecutar setup en servidor
echo "⚙️  Configurando entorno..."
ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$SSH_USER@$SSH_HOST" "
    cd $REMOTE_DIR
    chmod +x setup_ubuntu.sh
    ./setup_ubuntu.sh
"

echo ""
echo "✅ Despliegue completado!"
echo "📍 Ubicacion: $REMOTE_DIR"
echo "🎬 Para ejecutar: ssh $SSH_USER@$SSH_HOST 'cd $REMOTE_DIR && python video_factory.py --test curiosidades'"
