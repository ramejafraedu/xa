#!/bin/bash
# Video Factory V16 - Ubuntu Server Setup Script
# Para Ubuntu 22.04/24.04 LTS
# Autor: Video Factory Team
# Fecha: Abril 2025

set -e

echo "🎬 Video Factory V16 - Ubuntu Setup"
echo "===================================="

# Colores para output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Funciones de utilidad
print_status() {
    echo -e "${GREEN}[✓]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[!]${NC} $1"
}

print_error() {
    echo -e "${RED}[✗]${NC} $1"
}

# Verificar root
if [ "$EUID" -eq 0 ]; then 
   print_error "No ejecutar como root. Usa: sudo ./setup_ubuntu.sh"
   exit 1
fi

# ============================================
# 1. ACTUALIZAR SISTEMA
# ============================================
echo ""
echo "📦 1. Actualizando sistema..."
sudo apt update && sudo apt upgrade -y
print_status "Sistema actualizado"

# ============================================
# 2. INSTALAR DEPENDENCIAS DEL SISTEMA
# ============================================
echo ""
echo "🔧 2. Instalando dependencias del sistema..."

sudo apt install -y \
    python3.11 \
    python3.11-venv \
    python3.11-dev \
    python3-pip \
    ffmpeg \
    git \
    curl \
    wget \
    htop \
    tree \
    unzip \
    build-essential \
    libssl-dev \
    libffi-dev \
    python3-tk

print_status "Dependencias del sistema instaladas"

# ============================================
# 2.5 INSTALAR NODE.JS / NPM (REQUERIDO POR REMOTION)
# ============================================
echo ""
echo "🧩 2.5. Instalando Node.js LTS y npm..."

NODE_VERSION="not-installed"
if command -v node &> /dev/null; then
    NODE_VERSION=$(node --version 2>/dev/null || echo "not-installed")
    print_status "Node.js ya instalado: $NODE_VERSION"
else
    curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash -
    sudo apt install -y nodejs
    NODE_VERSION=$(node --version 2>/dev/null || echo "not-installed")
fi

NPM_VERSION=$(npm --version 2>/dev/null || echo "not-installed")
NPX_VERSION=$(npx --version 2>/dev/null || echo "not-installed")
print_status "Node.js: $NODE_VERSION | npm: $NPM_VERSION | npx: $NPX_VERSION"

NODE_MAJOR=$(echo "$NODE_VERSION" | sed -E 's/^v([0-9]+).*/\1/' 2>/dev/null || echo "0")
if ! [[ "$NODE_MAJOR" =~ ^[0-9]+$ ]]; then
    NODE_MAJOR=0
fi
if [ "$NODE_MAJOR" -lt 20 ]; then
    print_error "Node.js 20+ es obligatorio para Remotion (actual: $NODE_VERSION)"
    exit 1
fi

# ============================================
# 3. VERIFICAR FFMPEG
# ============================================
echo ""
echo "🎥 3. Verificando FFmpeg..."

if ! command -v ffmpeg &> /dev/null; then
    print_error "FFmpeg no está instalado correctamente"
    exit 1
fi

FFMPEG_VERSION=$(ffmpeg -version | head -n1 | cut -d' ' -f3)
print_status "FFmpeg instalado: versión $FFMPEG_VERSION"

if ! command -v ffprobe &> /dev/null; then
    print_error "ffprobe no está disponible. Reinstala paquete ffmpeg"
    exit 1
fi
FFPROBE_VERSION=$(ffprobe -version | head -n1 | cut -d' ' -f3)
print_status "ffprobe instalado: versión $FFPROBE_VERSION"

# ============================================
# 4. CREAR ESTRUCTURA DE DIRECTORIOS
# ============================================
echo ""
echo "📁 4. Creando estructura de directorios..."

PROJECT_DIR="$HOME/video_factory"
mkdir -p "$PROJECT_DIR"/{data,logs,temp,output,nichos,skills}
mkdir -p "$PROJECT_DIR/data/job_state"

print_status "Directorios creados en $PROJECT_DIR"

# ============================================
# 5. CONFIGURAR VIRTUAL ENVIRONMENT
# ============================================
echo ""
echo "🐍 5. Configurando entorno virtual Python..."

cd "$PROJECT_DIR"

# Crear virtual environment
if [ ! -d "venv" ]; then
    python3.11 -m venv venv
fi
source venv/bin/activate

# Upgrade pip
pip install --upgrade pip setuptools wheel

print_status "Entorno virtual creado"

# Instalar deps de Remotion si el composer existe en el repo.
if [ -d "$PROJECT_DIR/remotion-composer" ]; then
    echo ""
    echo "🎬 Instalando dependencias y build de Remotion..."
    cd "$PROJECT_DIR/remotion-composer"
    if [ -f "package-lock.json" ]; then
        npm ci || print_warning "npm ci en remotion-composer fallo (reintentando con npm install)"
    fi
    if [ ! -d "node_modules" ]; then
        npm install || print_warning "npm install en remotion-composer fallo"
    fi

    if [ ! -d "node_modules" ]; then
        print_error "node_modules no existe en remotion-composer después de instalar dependencias"
        exit 1
    fi

    if npm run | grep -q " build"; then
        npm run build || print_warning "npm run build fallo en remotion-composer"
    else
        print_warning "package.json sin script build; se omite npm run build"
    fi

    cd "$PROJECT_DIR"
fi

# ============================================
# 6. INSTALAR DEPENDENCIAS PYTHON
# ============================================
echo ""
echo "📚 6. Instalando dependencias Python..."

# Instalar PyTorch (CPU version para servidor sin GPU)
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu

# Instalar WhisperX (opcional, para sincronización de subtítulos)
print_warning "Instalando WhisperX (puede tardar varios minutos)..."
pip install whisperx || print_warning "WhisperX installation failed, continuing..."

# Dependencias principales
cat > requirements.txt << 'EOF'
# Core
pydantic>=2.0.0
pydantic-settings>=2.0.0
python-dotenv>=1.0.0
loguru>=0.7.0

# HTTP/Requests
requests>=2.31.0
httpx>=0.25.0
aiohttp>=3.9.0

# Media Processing
moviepy>=1.0.3
opencv-python>=4.8.0
Pillow>=10.0.0
ffmpeg-python>=0.2.0

# LLM/AI
google-genai>=1.14.0
google-cloud-texttospeech>=2.17.2
google-cloud-aiplatform>=1.60.0
openai>=1.0.0

# Data Processing
pandas>=2.0.0
numpy>=1.24.0

# Database
supabase>=2.0.0

# CLI/UI
rich>=13.0.0
typer>=0.9.0
fastapi>=0.104.0
uvicorn>=0.24.0

# Utilities
pyyaml>=6.0.1
python-multipart>=0.0.6
jinja2>=3.1.2

# Monitoring
psutil>=5.9.0
EOF

pip install -r requirements.txt

print_status "Dependencias Python instaladas"

# ============================================
# 7. CREAR ARCHIVO .ENV DE EJEMPLO
# ============================================
echo ""
echo "⚙️  7. Creando archivo .env de ejemplo..."

cat > .env.example << 'EOF'
# ==========================================
# VIDEO FACTORY V16 - CONFIGURATION
# Ubuntu Server Edition
# ==========================================

# === AI / LLM PROVIDERS ===
# Google Gemini (Primary LLM)
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_API_KEY2=optional_backup_key
GEMINI_API_KEY3=optional_backup_key
GEMINI_API_KEY4=optional_backup_key
PRIMARY_LLM=gemini-3.1-pro-preview
GEMINI_CHAT_MODELS=gemini-3.1-pro-preview,gemini-2.5-pro,gemini-2.5-flash,gemini-2.0-flash-001
GEMINI_TEXT_MODEL=gemini-3.1-pro-preview
GEMINI_VISION_MODEL=gemini-2.5-pro
GEMINI_TTS_MODEL=gemini-2.5-flash-preview-tts
IMAGE_GENERATION_MODEL=gemini-2.0-flash-preview-image-generation

# Vertex AI (opt-in)
USE_VERTEX_AI=false
VERTEX_PROJECT_ID=
VERTEX_LOCATION=global
GOOGLE_APPLICATION_CREDENTIALS=

# OpenRouter (Fallback LLM)
OPENROUTER_API_KEY=your_openrouter_key_here

# === TEXT TO SPEECH ===
# ElevenLabs (Primary TTS - requires API key)
ELEVENLABS_API_KEY=your_elevenlabs_key_here
ELEVENLABS_VOICE_ID=EXAVITQu4vr4xnSDxMaL
ELEVENLABS_MODEL_ID=eleven_multilingual_v2
ELEVENLABS_STABILITY=0.45
ELEVENLABS_SIMILARITY_BOOST=0.75

# Google Cloud TTS (fallback after ElevenLabs)
USE_GOOGLE_TTS=false
GOOGLE_TTS_API_KEY=
GOOGLE_TTS_SERVICE_ACCOUNT_JSON=
GOOGLE_TTS_VOICE_NAME=es-US-Neural2-A
GOOGLE_TTS_LANGUAGE_CODE=es-US
GOOGLE_TTS_SPEAKING_RATE=1.0
GOOGLE_TTS_PITCH=0.0
GOOGLE_TTS_TIMEOUT_SECONDS=45

# === VIDEO STOCK PROVIDERS ===
# Pexels (200 requests/hour free)
PEXELS_API_KEY=your_pexels_key_here
PEXELS_API_KEY2V=optional_backup
PEXELS_API_KEY3V=optional_backup
PEXELS_API_KEY4V=optional_backup

# Pixabay (100 requests/minute free)
PIXABAY_API_KEY=your_pixabay_key_here

# === DATABASE ===
# Supabase (for memory and metrics)
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_ANON_KEY=your_supabase_anon_key_here
SUPABASE_VIDEOS_TABLE=videos
SUPABASE_PERFORMANCE_TABLE=video_performance

# === NOTIFICATIONS ===
# Telegram Bot (optional)
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=your_chat_id_here

# === TRENDING DATA ===
# RapidAPI (for TikTok trending)
RAPIDAPI_KEY=your_rapidapi_key_here
ENABLE_TIKTOK_TRENDING_API=false

# === FEATURE FLAGS (NEW IN V16) ===
# Fact Verification (protects from misinformation)
ENABLE_FACT_VERIFICATION=true
FACT_VERIFICATION_MODE=blocking  # blocking | warning | info
FACT_VERIFICATION_MIN_SCORE=60
FACT_VERIFICATION_SKIP_FOR_NICHOS=

# Memory Management (for 8GB RAM server)
MAX_RAM_PERCENT_PER_JOB=20.0
ENABLE_MEMORY_STREAMING=true
FRAME_BUFFER_SECONDS=30
FORCE_GC_BETWEEN_STAGES=true

# === EXECUTION MODE ===
# free_mode: Use only free providers
FREE_MODE=false
ALLOW_FREEMIUM_IN_FREE_MODE=true

# === SCHEDULER ===
SCHEDULER_ENABLED=true
SCHEDULER_INTERVAL_MINUTES=360  # Every 6 hours
SCHEDULER_CANARY_MODE=false
SCHEDULER_CANARY_NICHOS=
SCHEDULER_USE_V15=true

# === IMAGE GENERATION ===
# Pollinations (free)
POLLINATIONS_BASE=https://image.pollinations.ai
PREFER_STOCK_IMAGES=true

# === REMOTION RENDERER ===
USE_REMOTION=true
FORCE_FFMPEG_RENDERER=false
REQUIRE_REMOTION=true
ALLOW_FFMPEG_FALLBACK=false

# === LOGGING ===
LOG_LEVEL=INFO
LOG_RETENTION_DAYS=7
EOF

# Crear .env vacío (usuario debe llenarlo)
if [ ! -f .env ]; then
    cp .env.example .env
    print_warning "Archivo .env creado. EDITA ESTE ARCHIVO con tus API keys antes de ejecutar."
fi

print_status "Archivo .env.example creado"

# ============================================
# 8. CREAR SYSTEMD SERVICE
# ============================================
echo ""
echo "🔧 8. Configurando servicio systemd..."

SERVICE_FILE="/tmp/video-factory.service"
DASHBOARD_SERVICE_FILE="/tmp/video-factory-dashboard.service"

cat > "$SERVICE_FILE" << EOF
[Unit]
Description=Video Factory V16 Scheduler
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$PROJECT_DIR
Environment=PATH=$PROJECT_DIR/venv/bin:/usr/local/bin:/usr/bin
Environment=PYTHONPATH=$PROJECT_DIR
Environment=HOME=$HOME
ExecStart=$PROJECT_DIR/venv/bin/python $PROJECT_DIR/scheduler.py
Restart=always
RestartSec=30
StartLimitInterval=60s
StartLimitBurst=3

# Memory limits (for 8GB server)
MemoryMax=6G
MemorySwapMax=2G

[Install]
WantedBy=multi-user.target
EOF

print_status "Archivo de servicio creado"
cat > "$DASHBOARD_SERVICE_FILE" << EOF
[Unit]
Description=Video Factory V16 Dashboard
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$PROJECT_DIR
Environment=PATH=$PROJECT_DIR/venv/bin:/usr/local/bin:/usr/bin
Environment=PYTHONPATH=$PROJECT_DIR
Environment=HOME=$HOME
ExecStart=$PROJECT_DIR/venv/bin/python $PROJECT_DIR/dashboard.py
Restart=always
RestartSec=10
StartLimitInterval=60s
StartLimitBurst=5

[Install]
WantedBy=multi-user.target
EOF

print_status "Archivo de servicio de dashboard creado"
print_warning "Para instalar el servicio ejecuta:"
echo "  sudo cp /tmp/video-factory.service /etc/systemd/system/"
echo "  sudo cp /tmp/video-factory-dashboard.service /etc/systemd/system/"
echo "  sudo systemctl daemon-reload"
echo "  sudo systemctl enable video-factory"
echo "  sudo systemctl start video-factory"
echo "  sudo systemctl enable video-factory-dashboard"
echo "  sudo systemctl start video-factory-dashboard"

# ============================================
# 9. CREAR SCRIPTS DE UTILIDAD
# ============================================
echo ""
echo "📜 9. Creando scripts de utilidad..."

# Script para verificar salud
cat > check_health.sh << 'EOF'
#!/bin/bash
# Health check script

echo "🏥 Video Factory Health Check"
echo "=============================="

cd "$(dirname "$0")"
source venv/bin/activate

# Verificar imports críticos
python3 -c "
import sys
try:
    from config import settings
    print('✅ Config OK')
    from agents.research_agent import ResearchAgent
    print('✅ Research Agent OK')
    from agents.script_agent import ScriptAgent
    print('✅ Script Agent OK')
    from pipeline.tts_engine import generate_tts
    print('✅ TTS Engine OK')
    from pipeline.video_stock import fetch_stock_videos
    print('✅ Video Stock OK')
    print('')
    print('All critical components loaded successfully!')
except Exception as e:
    print(f'❌ Error: {e}')
    sys.exit(1)
"

# Verificar espacio en disco
echo ""
echo "💾 Disk Usage:"
df -h "$HOME" | grep -v "Filesystem"

# Verificar RAM
echo ""
echo "🧠 Memory:"
free -h | grep -E "Mem|Swap"

# Verificar FFmpeg
echo ""
echo "🎥 FFmpeg:"
ffmpeg -version | head -n 1
ffprobe -version | head -n 1

# Verificar Node stack para Remotion
echo ""
echo "🎬 Node/Remotion stack:"
node --version
npm --version
npx --version
if [ -d "remotion-composer/node_modules" ]; then
    echo "✅ remotion-composer/node_modules presente"
else
    echo "❌ remotion-composer/node_modules no existe"
fi
EOF

chmod +x check_health.sh

# Script para limpiar temporales
cat > cleanup.sh << 'EOF'
#!/bin/bash
# Cleanup script for temp files

echo "🧹 Cleaning up temp files..."

cd "$(dirname "$0")"

# Limpiar temporales antiguos (> 24 horas)
find temp/ -type f -mtime +1 -delete 2>/dev/null || true
find temp/ -type d -empty -delete 2>/dev/null || true

# Limpiar logs antiguos (> 7 días)
find logs/ -name "*.log" -mtime +7 -delete 2>/dev/null || true

echo "✅ Cleanup complete"

# Mostrar espacio usado
echo ""
echo "Disk usage after cleanup:"
du -sh temp/ logs/ 2>/dev/null || true
EOF

chmod +x cleanup.sh

print_status "Scripts de utilidad creados"

# ============================================
# 10. INSTRUCCIONES FINALES
# ============================================
echo ""
echo "🎉 ¡Setup completado!"
echo "====================="
echo ""
echo "Próximos pasos:"
echo ""
echo "1. 🔑 Configura tus API keys:"
echo "   nano $PROJECT_DIR/.env"
echo ""
echo "2. ✅ Verifica la instalación:"
echo "   cd $PROJECT_DIR"
echo "   ./check_health.sh"
echo ""
echo "3. 🚀 Inicia el scheduler manualmente (para probar):"
echo "   source venv/bin/activate"
echo "   python scheduler.py"
echo ""
echo "4. 🔧 Instala el servicio systemd (para producción):"
echo "   sudo cp /tmp/video-factory.service /etc/systemd/system/"
echo "   sudo cp /tmp/video-factory-dashboard.service /etc/systemd/system/"
echo "   sudo systemctl daemon-reload"
echo "   sudo systemctl enable video-factory"
echo "   sudo systemctl start video-factory"
echo "   sudo systemctl enable video-factory-dashboard"
echo "   sudo systemctl start video-factory-dashboard"
echo ""
echo "5. 📊 Monitorea los logs:"
echo "   sudo journalctl -u video-factory -f"
echo "   sudo journalctl -u video-factory-dashboard -f"
echo "   # o si ejecutas manualmente:"
echo "   tail -f logs/video_factory.log"
echo ""
echo "6. 🧹 Programa limpieza automática (cron):"
echo "   crontab -e"
echo "   # Agrega: 0 3 * * * $PROJECT_DIR/cleanup.sh"
echo ""
echo "📚 Documentación:"
echo "   - Guía completa: ARQUITECTURA_SISTEMA.md"
echo "   - API Reference: docs/api.md"
echo "   - Troubleshooting: docs/troubleshooting.md"
echo ""
echo "💡 Tips para tu servidor (8GB RAM):"
echo "   - MAX_RAM_PERCENT_PER_JOB=20 (configurado)"
echo "   - Genera 1 video a la vez"
echo "   - Videos de 30-90 segundos recomendados"
echo "   - Usa ENABLE_MEMORY_STREAMING=true"
echo ""
echo "🎬 ¡Listo para crear contenido viral!"
