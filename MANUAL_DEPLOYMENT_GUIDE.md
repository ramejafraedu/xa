# 📤 GUÍA DE DESPLIEGUE MANUAL - Fix Remotion V15

## 🚨 Problema de Conectividad
La VM (35.239.64.169) está teniendo timeouts en SSH/SCP. 

## ✅ Solución: 3 Métodos Alternativos

---

### 🔧 MÉTODO 1: Edición Directa en VM (Más Rápido - 5 min)

Conectarse a la VM y editar los archivos directamente con nano/vim:

```bash
ssh xavierfranmen@35.239.64.169
cd ~/video_factory

# 1. Editar config.py - Agregar al final de la sección Remotion
nano config.py
```

**Agregar estas 3 líneas:**
```python
    # Remotion Frame Cache Crash Recovery (V15 Bug Fix)
    remotion_frame_cache_auto_recovery: bool = True
    remotion_frame_cache_max_retries: int = 2
    remotion_frame_cache_force_fallback: bool = False
```

**Guardar:** Ctrl+O, Enter, Ctrl+X

```bash
# 2. Ejecutar limpieza de caché
rm -rf ~/.cache/remotion
rm -rf remotion-composer/.cache
rm -rf remotion-composer/public/workspace/*

# 3. Forzar FFmpeg y reintentar
export ALLOW_FFMPEG_FALLBACK=true
python main.py --job-id historia_1776216130309
```

---

### 📦 MÉTODO 2: Git Push + Pull (Si tienes git)

En tu máquina local:
```bash
cd "C:\Users\ramej\OneDrive\Escritorio\Nueva carpeta\video_factory"
git add pipeline/renderer_remotion.py config.py
git commit -m "Fix: Remotion frame_cache crash recovery V15"
git push origin main
```

En la VM:
```bash
ssh xavierfranmen@35.239.64.169
cd ~/video_factory
git pull
bash fix_remotion_frame_cache.sh
python main.py --job-id historia_1776216130309
```

---

### 💾 MÉTODO 3: Usar Panel Web (Si está disponible)

Si tienes acceso al panel web de Video Factory en http://35.239.64.169:8000:

1. Ve a "Configuración" o "Settings"
2. Busca opciones de Remotion
3. Cambia:
   - `ALLOW_FFMPEG_FALLBACK` = `true`
   - O deshabilita Remotion temporalmente
4. Reintenta el job desde el panel

---

## 🎯 SOLUCIÓN MÁS RÁPIDA (Copiar y Pegar)

Si solo quieres desbloquear el video AHORA:

```bash
# Conectar a VM (reintentar hasta que funcione)
ssh xavierfranmen@35.239.64.169

# Una vez dentro, ejecutar:
cd ~/video_factory
export ALLOW_FFMPEG_FALLBACK=true
python main.py --job-id historia_1776216130309
```

**Esto usará FFmpeg en lugar de Remotion y completará el video.**

---

## 📋 Resumen de Cambios Locales (Ya Hechos)

Los siguientes archivos fueron modificados y están listos en tu máquina:

1. ✅ `pipeline/renderer_remotion.py` (+247 líneas de recovery)
2. ✅ `config.py` (+3 configuraciones nuevas)
3. ✅ `fix_remotion_frame_cache.sh` (script de reparación)

**Archivos creados para documentación:**
- `REMOTION_V15_FRAME_CACHE_FIX.md` - Documentación técnica completa
- `APPLY_FIX_MANUAL.md` - Guía de aplicación manual
- `QUICK_FIX_VM_COMMANDS.txt` - Comandos rápidos para copiar/pegar

---

## ⚠️ Si Nada Funciona

Si la VM sigue sin responder:

1. **Verifica el estado de la VM** en Google Cloud Console
2. **Reinicia la VM** desde el panel de GCP si es necesario
3. **Verifica reglas de firewall** - el puerto 22 (SSH) debe estar abierto
4. **IP pública** - verifica que la IP 35.239.64.169 sigue siendo la correcta

---

## 📞 Próximos Pasos

1. Intenta conectarte a la VM directamente
2. Si funciona, ejecuta los comandos de limpieza
3. Reintenta el render con `ALLOW_FFMPEG_FALLBACK=true`
4. El video debe completarse y estar disponible en `workspace/output/`

**Job bloqueado:** `historia_1776216130309`  
**Título:** "El asesino serial que ganó un show de citas"  
**Calidad:** 9.0/10 - Listo para renderizar
