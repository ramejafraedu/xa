# Documento de Contexto y Estado Actual

Fecha de corte: 2026-04-05
Proyecto: video_factory
Workspace: C:\Users\ramej\OneDrive\Escritorio\Nueva carpeta\video_factory
SO: Windows

## 1. Resumen Ejecutivo

- El proyecto es una fabrica automatizada de videos verticales con pipeline por etapas (contenido, QA, TTS, subtitulos, media, render, publicacion).
- El sistema esta corriendo con procesos Python activos y actividad reciente de scheduler.
- El estado de versionado esta bloqueado para push: no existe repositorio Git inicializado en esta carpeta (no hay .git).
- El estado operativo actual muestra un job reciente en error:
  - job_id: salud_1775433600050
  - etapa reportada: unknown
  - error: [WinError 2] El sistema no puede encontrar el archivo especificado
  - traza apunta al modulo de imagenes durante copia de archivo legacy.

## 2. Entorno Tecnico Actual

- Python detectado: 3.14.3
- Dependencias base (requirements):
  - pydantic / pydantic-settings
  - python-dotenv
  - loguru
  - typer + rich
  - httpx
  - edge-tts
  - apscheduler
  - fastapi + uvicorn + sse-starlette
  - aiofiles
  - google-genai
- Observacion: hay modulos opcionales comentados para WhisperX, pytrends, Google Drive/Sheets y Supabase.

## 3. Estado de Git y GitHub

- Estado actual: la carpeta no es un repo Git.
- Evidencia:
  - git status -sb -> fatal: not a git repository
  - git branch --show-current -> fatal: not a git repository
  - git remote -v -> fatal: not a git repository
- Impacto:
  - No se puede hacer commit ni push a GitHub hasta inicializar Git y conectar remoto.

## 4. Estructura Funcional del Proyecto

- Entrada principal:
  - video_factory.py: orquestacion CLI y pipeline completo por nicho.
- Configuracion:
  - config.py: carga .env, validaciones, rutas, toggles de proveedores, definicion de 5 nichos.
  - models/config_models.py: modelos Pydantic de nicho y app.
- Estado y recuperacion:
  - state_manager.py: checkpoints por stage, idempotencia, manifests resumibles.
- Pipeline:
  - pipeline/content_gen.py
  - pipeline/quality_gate.py
  - pipeline/self_healer.py
  - pipeline/tts_engine.py
  - pipeline/subtitles.py
  - pipeline/image_gen.py
  - pipeline/video_stock.py
  - pipeline/music.py
  - pipeline/sfx.py
  - pipeline/renderer.py
  - pipeline/pre_render_validator.py
  - pipeline/duration_validator.py
  - pipeline/cleanup.py
- Ejecucion programada y dashboard:
  - scheduler.py: APScheduler con timezone America/Mexico_City.
  - dashboard.py: API FastAPI + SSE para monitoreo.

## 5. Configuracion de Nichos (Actual)

Desde config.py se observan 5 nichos activos:

- finanzas: plataforma tiktok_reels, horas [7, 15, 23]
- historia: plataforma tiktok_reels, horas [8, 16, 0]
- curiosidades: plataforma tiktok_reels, horas [9, 17, 1]
- salud: plataforma facebook, horas [10, 18, 2]
- recetas: plataforma facebook, horas [11, 19, 3]

## 6. Estado Operativo Observado

### 6.1 Procesos activos

- Hay 2 procesos python.exe activos al momento del corte.
- Horas de inicio observadas:
  - 16:21:55
  - 16:31:45

### 6.2 Logs recientes

- Se observan checks periodicos de FFmpeg y espacio en disco ejecutandose de forma continua.
- El scheduler dispara jobs por hora segun nicho configurado.
- En la corrida de salud de las 18:00:
  - content_gen y quality_gate completados
  - tts y subtitles completados
  - media inicia y genera imagenes
  - luego ocurre excepcion WinError 2 en image_gen.py al copiar imagen legacy.

### 6.3 Manifest activo en temp

- Archivo: workspace/temp/job_manifest_salud_1775433600050.json
- Estado: error
- Datos clave:
  - quality_score: 8.4
  - viral_score: 9.0
  - tts_engine_used: gemini
  - duration_seconds: 49.130958
  - error_stage: unknown
  - error_code: UNKNOWN
  - error_message: [WinError 2] El sistema no puede encontrar el archivo especificado

## 7. Incidencias Activas y Senales de Riesgo

1. Falla de archivo en pipeline de imagenes

- Sintoma: FileNotFoundError [WinError 2]
- Punto de falla reportado: pipeline/image_gen.py en shutil.copy2(results[0], legacy)
- Riesgo: jobs programados pueden terminar en error intermitente durante stage media.

2. Integracion Supabase con 404

- Sintoma: read_memory y save_result devuelven 404 en endpoints /rest/v1/videos y /rest/v1/video_performance.
- Riesgo: perdida de memoria historica y telemetria persistente.

3. Endpoint TikTok trending con 404

- Sintoma: https://tiktok-trending.p.rapidapi.com/feed/list devuelve 404.
- Riesgo: contexto de tendencias incompleto (se mantiene RSS de Google como fallback).

4. Pollinations con timeout/429 recurrente

- Sintoma: timeout y rate limit en descargas de imagen.
- Mitigacion existente: fallback a Leonardo y circuito de proteccion.

5. Notificaciones Telegram con error 400 (observado en ejecucion previa)

- Riesgo: alertas de exito/error pueden no llegar de forma consistente.

## 8. Estado de Artefactos en Disco

- workspace/output:
  - solo carpeta review_manual detectada en este corte.
- workspace/temp:
  - imagen_2_1775433600050.jpg
  - imagen_3_1775433600050.jpg
  - imagen_4_1775433600050.jpg
  - job_manifest_salud_1775433600050.json

## 9. Seguridad y Configuracion Sensible

- Existe archivo .env en raiz (tamano > 7 KB), por lo que hay secretos locales cargados.
- Recomendacion operativa:
  - no exponer .env en commits
  - usar .env.example como plantilla publica

## 10. Bloqueadores Inmediatos

- Bloqueador de despliegue a GitHub:
  - falta inicializar Git y configurar remoto.
- Bloqueador de estabilidad de ejecucion:
  - error WinError 2 en pipeline/image_gen.py durante copia de imagen legacy.

## 11. Acciones Recomendadas (Orden Prioritario)

1. Inicializar repositorio Git en la raiz y conectar remoto de GitHub.
2. Corregir robustez de pipeline/image_gen.py antes de siguiente corrida programada.
3. Validar URLs y tablas reales de Supabase para eliminar 404.
4. Revisar endpoint/plan de RapidAPI para TikTok trending.
5. Verificar formato de payload a Telegram para corregir 400.

## 12. Fuentes Consultadas

- requirements.txt
- config.py
- models/config_models.py
- video_factory.py
- state_manager.py
- scheduler.py
- dashboard.py
- pipeline/image_gen.py
- logs/factory.log
- workspace/temp/job_manifest_salud_1775433600050.json
