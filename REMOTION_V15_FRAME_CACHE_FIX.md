# 🚨 Remotion V15 Frame Cache Crash - Resolución

## Error Reportado

```
thread '<unnamed>' panicked at rust/frame_cache.rs:257:43: 
called `Option::unwrap()` on a `None` value
```

**Contexto:** Error durante render de video "historia" (16 escenas, 54.4s) en VM Ubuntu 22.04.

---

## ✅ Solución Implementada

### 1. Auto-Recovery en Código (Automático)

Se agregó manejo automático del error en `pipeline/renderer_remotion.py`:

- **Detección automática** del error `frame_cache.rs`
- **Limpieza automática** del caché (`~/.cache/remotion`, etc.)
- **Reintento automático** con `concurrency=1`
- **Fallback configurable** a FFmpeg si persisten los errores

### 2. Nuevas Opciones de Configuración (config.py)

```python
# Remotion Frame Cache Crash Recovery (V15 Bug Fix)
remotion_frame_cache_auto_recovery: bool = True   # Auto-limpiar y reintentar
remotion_frame_cache_max_retries: int = 2         # Reintentos antes de fallback
remotion_frame_cache_force_fallback: bool = False # Forzar FFmpeg si falla
```

### 3. Script de Reparación Manual

```bash
# Ejecutar en la VM (35.239.64.169)
cd ~/video_factory
bash fix_remotion_frame_cache.sh
```

---

## 🛠️ Opciones de Mitigación (Elija una)

### Opción A: Forzar Fallback a FFmpeg (Rápido)

Para el video actual que falló, usar FFmpeg como respaldo:

```bash
# Opción 1: Variable de entorno
export ALLOW_FFMPEG_FALLBACK=true
python main.py --job-id historia_1776216130309

# Opción 2: Configuración permanente
# En config.py, cambiar:
remotion_frame_cache_force_fallback = True
```

### Opción B: Reducir Concurrencia (Recomendado)

El error puede deberse a race conditions. Reducir workers:

```python
# config.py
remotion_concurrency = 1  # Reducir de 8 a 1
```

### Opción C: Reparación Manual Completa

Si el auto-recovery falla, ejecutar en la VM:

```bash
cd ~/video_factory

# 1. Detener cualquier proceso de Remotion
pkill -f remotion 2>/dev/null || true
pkill -f "Compositor" 2>/dev/null || true

# 2. Limpiar cachés
rm -rf ~/.cache/remotion
rm -rf remotion-composer/.cache
rm -rf remotion-composer/node_modules/.cache
rm -rf remotion-composer/public/workspace/*

# 3. Limpiar temporales
rm -rf /tmp/remotion-*
rm -rf /tmp/.remotion-*

# 4. Verificar espacio
df -h /tmp

# 5. Reiniciar render
python main.py --job-id historia_1776216130309
```

---

## 🔍 Análisis Técnico del Error

### Causas Probables

1. **Race Condition:** El compositor Rust de Remotion tiene un bug de concurrencia en `frame_cache.rs`
2. **Caché Corrupto:** Archivo en `~/.cache/remotion` está corrupto
3. **Memoria Insuficiente:** La VM puede estar baja de RAM durante el bundling
4. **Bug de Versión:** Posible regresión en la versión específica de Remotion

### Patrón del Error

- Ocurre durante `Bundler` cuando copia assets al directorio público
- El compositor Rust (multi-threaded) falla en acceso a caché
- El `unwrap()` en línea 257 sugiere un `Option` inesperadamente `None`

### Soluciones Aplicadas

| Solución | Implementación | Estado |
|----------|----------------|--------|
| Auto-detection | `_is_frame_cache_error()` | ✅ Agregado |
| Auto-limpieza | `_clear_remotion_cache()` | ✅ Agregado |
| Auto-retry | `_run_remotion_with_recovery()` | ✅ Agregado |
| Config fallback | `remotion_frame_cache_force_fallback` | ✅ Configurable |

---

## 📊 Métricas a Monitorear

Después de aplicar la solución, verificar:

```bash
# Éxito del render
ls -lh workspace/output/historia_*.mp4

# Logs de recovery
grep -i "frame_cache\|recovery\|retry" logs/pipeline.log | tail -20

# Uso de recursos durante render
htop  # o
dstat -cmdny 1
```

---

## 🔄 Recomendación Inmediata

Para resolver el video "historia_1776216130309" bloqueado:

```bash
# Conectar a VM
ssh -i id_ed25519_xavito xavierfranmen@35.239.64.169

# Opción 1: Ejecutar script de reparación (más fácil)
cd ~/video_factory
bash fix_remotion_frame_cache.sh

# Luego reintentar el render
python main.py --job-id historia_1776216130309

# Opción 2: Forzar FFmpeg inmediatamente (si el deadline es crítico)
export ALLOW_FFMPEG_FALLBACK=true
python main.py --job-id historia_1776216130309
```

---

## 📝 Archivos Modificados

1. **`pipeline/renderer_remotion.py`** - Agregado auto-recovery
2. **`config.py`** - Nuevas opciones de configuración
3. **`fix_remotion_frame_cache.sh`** - Script de reparación manual
4. **`REMOTION_V15_FRAME_CACHE_FIX.md`** - Esta documentación

---

## ⚠️ Prevención Futura

Para evitar este error en producción:

1. **Reducir concurrencia** en VMs con recursos limitados:
   ```python
   remotion_concurrency = min(4, os.cpu_count())
   ```

2. **Monitorear caché** periódicamente:
   ```bash
   # Cron job para limpiar caché viejo
   find ~/.cache/remotion -mtime +7 -delete
   ```

3. **Considerar downgrade** de Remotion si persiste:
   ```bash
   cd remotion-composer
   npm install @remotion/core@4.0.180 @remotion/cli@4.0.180 --save
   ```

---

**Estado:** Solución implementada y lista para pruebas  
**Próximo paso:** Ejecutar script de reparación en la VM y reintentar el video
