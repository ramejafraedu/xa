# Changelog

Historial de cambios relevantes del proyecto Video Factory.

## V15 PRO (Abril 2026)

### Agregado

- Sistema multi&#45;agente: Research → Script → Scene → Assets → Editor.
- StoryState: memoria narrativa global para coherencia entre videos.
- Director mode: checkpoints interactivos (--director) para revision humana.
- Feedback Loop: revision de calidad AI con autoregeneracion.
- Dashboard web con API, SSE y monitoreo de recursos en tiempo real.
- Scheduler V15 con APScheduler y timezone America/Mexico_City.
- YAMLs por nicho en `nichos/` — editables sin tocar Python.
- Validacion Pydantic completa de YAMLs al cargar (fail-fast en startup).
- Retry con backoff exponencial + circuit breaker en http_client.
- Remotion como renderer premium con fallback a FFmpeg.
- OpenMontage free tools (silence cutter, color grade, auto reframe).
- Fallback a FFmpeg cuando Remotion produce audio silencioso o desincronizado.
- `--test` ahora acepta nicho opcional: `--test curiosidades`.
- `clean_tts_text()` movido a `pipeline/tts_engine.py` (donde pertenece).
- Refactor de `run_pipeline()` en 10 metodos privados por stage.
- CI basico con py_compile + ruff en GitHub Actions.
- CHANGELOG.md.

### Cambiado

- README actualizado a V15 con arbol de directorios y flags nuevos.
- Subtitulos robustos con script-locked timing (ASS).
- Edge-TTS fallback con cooldown tras errores 403.
- Gemini TTS con rotacion de 4 keys.
- `.gitignore` actualizado para workspace/image_cache y music_cache.

### Corregido

- Fallback a FFmpeg cuando Remotion output esta en silencio o desincronizado.
- Dashboard tolera psutil opcional.
- Sanitizacion de subtitulos para evitar crashes en Remotion.
- AssetAgent: correccion de crash en generacion de imagenes cuando `visual_language.primary_colors` viene como lista (YAML) en lugar de diccionario.

### Validado (Fase 1 cierre servidor - 11 Abril 2026)

- Corrida end-to-end `finanzas` en servidor completada en estado `success`.
- Integracion de validacion pre-render por ToolRegistry estabilizada para esquema V15 (`scenes`) sin bloquear por mismatch legacy (`cuts`).
- Post-render QA en servidor aprobado con salida final MP4 y manifest archivado en `workspace/output`.
- Observabilidad A/B extendida con metadatos de seleccion (variant/decision/score) en manifest y dashboard.
- Resumen baseline -> post-cambios documentado: de error `pre_render_validation/ASSET_MISSING` a ejecucion completa con render Remotion + QA ok.

---

## V14 (Marzo 2026)

### Agregado

- Pipeline completo end-to-end para 5 nichos.
- Checkpoints por etapa con StateManager y resume de jobs fallidos.
- Idempotencia por hashes de entrada — rerun seguro.
- Pre-render y post-render QA automatizado.
- Self-healer reactivo para contenido, TTS y render.
- Publicacion automatica a Telegram, Google Drive y Sheets.
- Supabase para memoria y persistencia de resultados.
- Pexels multi-key rotation + Pixabay + Coverr fallback.
- A/B testing automatico por variante de gancho.
- Modo dry-run para generacion de contenido sin render.
- Cost governance freemium con presupuesto diario/mensual.

### Cambiado

- Migracion de n8n a Python puro con Typer CLI.
- Audio filters identicos a MASTER V13 via FFmpeg.

---

## V12–V13 (Febrero 2026)

- Pipeline inicial en n8n con nodos separados.
- Audio processing con FFmpeg filters (MASTER V13).
- Primeras integraciones con Pexels y Edge-TTS.
