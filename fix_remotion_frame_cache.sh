#!/bin/bash
# Script de reparación para error Remotion frame_cache.rs crash
# Video Factory V15 - Bug Fix

echo "=========================================="
echo "🔧 Remotion Frame Cache Repair Script"
echo "=========================================="
echo ""

# Verificar que estamos en el directorio correcto
if [ ! -f "pipeline/renderer_remotion.py" ]; then
    echo "❌ Error: Debe ejecutar este script desde el directorio video_factory/"
    exit 1
fi

echo "📍 Directorio de trabajo: $(pwd)"
echo ""

# 1. Limpiar cachés de Remotion
echo "🧹 Paso 1: Limpiando cachés de Remotion..."

# Caché global
if [ -d "$HOME/.cache/remotion" ]; then
    rm -rf "$HOME/.cache/remotion"
    echo "   ✅ Caché global limpiado: ~/.cache/remotion"
else
    echo "   ℹ️  No existe caché global"
fi

# Caché local
if [ -d "remotion-composer/.cache" ]; then
    rm -rf remotion-composer/.cache
    echo "   ✅ Caché local limpiado: remotion-composer/.cache"
fi

if [ -d "remotion-composer/node_modules/.cache" ]; then
    rm -rf remotion-composer/node_modules/.cache
    echo "   ✅ Caché de node_modules limpiado"
fi

# Workspace cache
if [ -d "remotion-composer/public/workspace" ]; then
    rm -rf remotion-composer/public/workspace/*
    echo "   ✅ Workspace cache limpiado"
fi

# 2. Verificar integridad de node_modules
echo ""
echo "🔍 Paso 2: Verificando instalación de Remotion..."

if [ ! -d "remotion-composer/node_modules/@remotion/cli/dist" ]; then
    echo "   ⚠️  Node modules incompleto. Reinstalando..."
    cd remotion-composer
    npm install --no-audit --no-fund 2>&1 | tail -10
    cd ..
    echo "   ✅ Reinstalación completada"
else
    echo "   ✅ Node modules verificado"
fi

# 3. Verificar permisos del compositor
echo ""
echo "🔐 Paso 3: Verificando permisos..."

COMPOSITOR_BIN="remotion-composer/node_modules/@remotion/renderer/dist/compositor"
if [ -f "$COMPOSITOR_BIN" ]; then
    chmod +x "$COMPOSITOR_BIN" 2>/dev/null
    echo "   ✅ Permisos de compositor verificados"
fi

# 4. Verificar espacio en disco
echo ""
echo "💾 Paso 4: Verificando espacio en disco..."

AVAILABLE=$(df -h /tmp | tail -1 | awk '{print $4}')
echo "   Espacio disponible en /tmp: $AVAILABLE"

AVAILABLE_GB=$(df -BG /tmp | tail -1 | awk '{print $4}' | sed 's/G//')
if [ "$AVAILABLE_GB" -lt 5 ]; then
    echo "   ⚠️  ADVERTENCIA: Menos de 5GB disponibles. Limpiando /tmp..."
    rm -rf /tmp/remotion-* 2>/dev/null
    rm -rf /tmp/.remotion-* 2>/dev/null
    echo "   ✅ /tmp limpiado"
fi

# 5. Mostrar resumen
echo ""
echo "=========================================="
echo "✅ Reparación completada"
echo "=========================================="
echo ""
echo "Próximos pasos:"
echo ""
echo "1. Si el error persiste, reinicie el pipeline con:"
echo "   python main.py --job-id historia_1776216130309"
echo ""
echo "2. Para forzar fallback a FFmpeg (emergencia):"
echo "   export ALLOW_FFMPEG_FALLBACK=true"
echo "   # o modificar en config.py:"
echo "   remotion_frame_cache_force_fallback = True"
echo ""
echo "3. Para reducir concurrencia de Remotion:"
echo "   # En config.py, cambiar:"
echo "   remotion_concurrency = 1  # En lugar de 8"
echo ""
echo "4. Para verificar que funciona, probar con:"
echo "   cd remotion-composer"
echo "   npx remotion render src/index.tsx --help"
echo ""
