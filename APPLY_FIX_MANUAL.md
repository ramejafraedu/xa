# 📋 APLICAR FIX MANUAL - Remotion Frame Cache Crash

## ⚠️ Problema
Video "historia_1776216130309" bloqueado en etapa de render por error de Remotion:
```
thread '<unnamed>' panicked at rust/frame_cache.rs:257:43
```

---

## 🎯 SOLUCIÓN RÁPIDA (2 minutos)

### Opción 1: Forzar Fallback a FFmpeg (Inmediata)

Ejecutar **directamente en la VM** (35.239.64.169):

```bash
cd ~/video_factory
export ALLOW_FFMPEG_FALLBACK=true
python main.py --job-id historia_1776216130309
```

**Resultado:** El video se renderizará con FFmpeg en lugar de Remotion.

---

### Opción 2: Reparar y Reintentar con Remotion (Recomendada)

```bash
# 1. Conectar a VM
ssh xavierfranmen@35.239.64.169

# 2. Ir al directorio del proyecto
cd ~/video_factory

# 3. Ejecutar script de reparación
bash fix_remotion_frame_cache.sh

# 4. Reintentar el render
python main.py --job-id historia_1776216130309
```

---

## 🔧 CAMBIOS EN CÓDIGO (Ya aplicados)

Los siguientes archivos fueron modificados para manejar automáticamente este error:

### 1. `pipeline/renderer_remotion.py`
- ✅ Agregada detección automática de `frame_cache.rs` error
- ✅ Agregada limpieza automática de caché corrupto
- ✅ Agregado reintento con `concurrency=1`
- ✅ Mejorado logging de diagnóstico

### 2. `config.py`
- ✅ Nuevas opciones configurables:
  - `remotion_frame_cache_auto_recovery = True`
  - `remotion_frame_cache_max_retries = 2`
  - `remotion_frame_cache_force_fallback = False`

### 3. `fix_remotion_frame_cache.sh`
- Script de reparación manual para ejecutar en la VM

---

## 🎬 ESTADO DEL VIDEO BLOQUEADO

**Job ID:** `historia_1776216130309`  
**Nicho:** historia  
**Título:** "El asesino serial que ganó un show de citas"  
**Calidad QA:** Hook=9.0 | Desarrollo=9.0 | Cierre=9.0 | Global=9.0  
**Estado:** Error en render (Remotion frame_cache crash)  
**Assets:** ✅ Listos (16 imágenes, audio, música, subtítulos)

El video está completamente preparado, solo falta el render final.

---

## 📤 SUBIR ARCHIVOS A LA VM

Si necesita subir los archivos modificados manualmente:

### Método A: Tar + SCP

```bash
# En su máquina local (Windows/PowerShell)
cd "C:\Users\ramej\OneDrive\Escritorio\Nueva carpeta\video_factory"

# Crear tarball con archivos modificados
tar -czf fix_remotion_v15.tar.gz pipeline/renderer_remotion.py config.py fix_remotion_frame_cache.sh

# Subir a VM (si SSH funciona)
scp -i id_ed25519_xavito fix_remotion_v15.tar.gz xavierfranmen@35.239.64.169:~/

# En la VM, extraer
ssh xavierfranmen@35.239.64.169
cd ~/video_factory
tar -xzf ~/fix_remotion_v15.tar.gz
```

### Método B: Git Push/Pull

```bash
# Si tiene git configurado
git add pipeline/renderer_remotion.py config.py
git commit -m "Fix: Remotion frame_cache crash recovery"
git push

# En la VM
git pull
```

### Método C: Copiar Manual con Nano/Vim

Si no hay acceso SCP, editar directamente en la VM:

```bash
ssh xavierfranmen@35.239.64.169

# Editar config.py
nano ~/video_factory/config.py
# Agregar al final de la sección Remotion:
# remotion_frame_cache_auto_recovery = True
# remotion_frame_cache_max_retries = 2
# remotion_frame_cache_force_fallback = False
```

---

## ✅ VERIFICACIÓN POST-FIX

Después de aplicar la solución:

```bash
# 1. Verificar que el código está actualizado
grep -n "_is_frame_cache_error" ~/video_factory/pipeline/renderer_remotion.py

# Debe mostrar: def _is_frame_cache_error(stderr_text: str) -> bool:

# 2. Verificar configuración
grep -n "remotion_frame_cache" ~/video_factory/config.py

# Debe mostrar las 3 nuevas líneas de configuración

# 3. Reintentar render
python main.py --job-id historia_1776216130309

# 4. Monitorear logs en tiempo real
tail -f ~/video_factory/logs/pipeline.log | grep -i "remotion\|frame_cache\|retry"
```

---

## 🚨 SI EL ERROR PERSISTE

Si después de aplicar el fix el error continúa:

### Acción 1: Reiniciar la VM
```bash
# En la VM
sudo reboot

# Esperar 2 minutos, reconectar, luego:
cd ~/video_factory
python main.py --job-id historia_1776216130309
```

### Acción 2: Downgrade de Remotion
```bash
cd ~/video_factory/remotion-composer
npm install @remotion/core@4.0.180 @remotion/cli@4.0.180 --save
cd ..
python main.py --job-id historia_1776216130309
```

### Acción 3: Usar FFmpeg permanentemente
```bash
# En config.py, cambiar:
use_remotion = False
force_ffmpeg_renderer = True

# Luego reintentar
python main.py --job-id historia_1776216130309
```

---

## 📞 CONTACTO

Si necesita asistencia adicional:
- **Documentación completa:** `REMOTION_V15_FRAME_CACHE_FIX.md`
- **Plan de reparación:** `.windsurf/plans/remotion-v15-frame-cache-fix-7bae8b.md`
- **Archivos modificados:**
  - `pipeline/renderer_remotion.py` (+150 líneas)
  - `config.py` (+3 configuraciones)
  - `fix_remotion_frame_cache.sh` (script de reparación)

---

**Resumen:** El fix está listo. Solo necesita ejecutar los comandos en la VM para desbloquear el video.
