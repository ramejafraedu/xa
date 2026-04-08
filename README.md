# Video Factory V15 PRO

Fabrica automatizada de videos verticales (formato short) con pipeline multi-agente por etapas, recuperacion por checkpoints y ejecucion manual, programada o desde dashboard web.

> **V14 → V15**: V14 es el pipeline clasico secuencial. V15 agrega un sistema multi-agente (Research → Script → Scene → Assets → Editor), director humano-en-el-loop, StoryState para coherencia narrativa y feedback loop con autoregenacion. V14 se mantiene como fallback con `--v14`.

## Caracteristicas

- Pipeline completo end-to-end para 5 nichos (V14 clasico + V15 multi-agent).
- Checkpoints por etapa con resume de jobs fallidos.
- Idempotencia por hashes para evitar reprocesos innecesarios.
- QA antes y despues del render.
- Dashboard web con API y logs en tiempo real (SSE).
- Scheduler 24/7 con APScheduler y timezone America/Mexico_City.
- Integraciones opcionales: Supabase, Google Drive/Sheets, Telegram.
- Validacion Pydantic de configuraciones YAML al arrancar.
- Retry con backoff exponencial + circuit breaker en todas las APIs externas.

## Nichos incluidos

- finanzas
- historia
- curiosidades
- historias_reddit
- ia_herramientas

Cada nicho se configura via YAML en `nichos/` — editable sin tocar Python.

## Stack tecnico

- Python + Typer (CLI)
- FastAPI + Uvicorn + SSE (dashboard)
- APScheduler (ejecucion programada)
- Loguru + Rich (observabilidad y UX en consola)
- Pydantic / pydantic-settings (configuracion, modelos y validacion YAML)
- httpx (HTTP con retry, backoff, circuit breaker)

## Estructura principal

```
.
├── video_factory.py         # Orquestador principal (CLI) — stages extraidos
├── scheduler.py             # Scheduler 24/7
├── dashboard.py             # Servidor dashboard/API
├── config.py                # Carga .env y settings globales
├── state_manager.py         # Checkpoints, manifests y resume
├── core/
│   ├── pipeline_v15.py      # Pipeline V15 multi-agente
│   └── director.py          # Director mode (human-in-the-loop)
├── agents/                  # Agentes V15 (script, scene, etc.)
├── pipeline/                # Etapas de generacion/render (TTS, subs, stock, etc.)
├── publishers/              # Telegram, Drive/Sheets
├── services/                # HTTP client, LLM router, trends, Supabase
├── models/                  # Pydantic models (content, config)
├── nichos/                  # Configuraciones YAML por nicho
├── schemas/                 # JSON schemas
├── tools/                   # OpenMontage tools
├── workspace/
│   ├── temp/                # Artefactos temporales por job
│   └── output/              # Videos y manifests finales
└── logs/                    # Logs rotados
```

## Flujo del pipeline (V14)

Etapas principales (cada una extraida a su propio metodo):

1. Lectura de memoria/contexto (`_stage_memory`)
2. Generacion de contenido (`_stage_content_gen`)
3. Quality gate + self-healing (`_stage_quality_gate`)
4. TTS (`_stage_tts`)
5. Subtitulos (`_stage_subtitles`)
6. Media — clips/imagenes/musica/sfx (`_stage_media`)
7. Descarga/combinacion de clips + pre-render validation (`_stage_download`)
8. Render + post-render QA (`_stage_render`)
9. Publicacion y persistencia (`_stage_publish`)
10. Limpieza y archivado de manifest (`_stage_cleanup`)

Cada job genera/actualiza un manifest JSON para auditoria y recuperacion.

## Requisitos

- Python 3.11+ (el proyecto se ha ejecutado en Python 3.14)
- FFmpeg instalado y disponible en PATH
- Acceso a APIs externas segun proveedores habilitados

## Instalacion

### 1) Clonar el repositorio

```bash
git clone https://github.com/ramejafraedu/xa.git
cd xa
```

### 2) Crear entorno virtual

En Windows (PowerShell):

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

En Linux/macOS:

```bash
python -m venv .venv
source .venv/bin/activate
```

### 3) Instalar dependencias

```bash
pip install -r requirements.txt
```

### 4) Configurar variables de entorno

```bash
copy .env.example .env
```

Completa al menos estas variables criticas para arrancar:

- GITHUB_TOKEN
- PEXELS_API_KEY (al menos una)

Variables recomendadas para mejores resultados:

- GEMINI_API_KEY
- TELEGRAM_BOT_TOKEN
- TELEGRAM_CHAT_ID

## Uso rapido (CLI)

Probar 1 video de finanzas:

```bash
python video_factory.py --test
```

Probar con un nicho especifico:

```bash
python video_factory.py --test curiosidades
```

Ejecutar un nicho ahora (V14 clasico):

```bash
python video_factory.py finanzas
```

Ejecutar con V15 multi-agente:

```bash
python video_factory.py --v15 finanzas
```

Modo director (interactivo, apruebas cada stage):

```bash
python video_factory.py --director finanzas
```

Forzar V14 clasico:

```bash
python video_factory.py --v14 finanzas
```

Ejecutar todos los nichos:

```bash
python video_factory.py --all-now
```

Modo dry-run (sin render):

```bash
python video_factory.py --dry-run finanzas
```

Reanudar job fallido:

```bash
python video_factory.py --resume JOB_ID
```

Iniciar scheduler 24/7:

```bash
python video_factory.py --schedule
```

## Dashboard web

Levantar dashboard en puerto 8000:

```bash
python dashboard.py
```

Puerto custom:

```bash
python dashboard.py --port 9000
```

Incluye endpoints para estado, nichos, jobs, ejecuciones y stream de logs.

Vista dedicada de manifest por job:

```bash
http://localhost:8000/job/<JOB_ID>/manifest
```

Monitoreo de recursos en tiempo real via SSE:

```bash
GET /api/resources/stream
```

Cost governance freemium:

- DAILY_BUDGET_USD para tope diario opcional.
- MONTHLY_BUDGET_USD (default recomendado: 1) para tope mensual en modo freemium.

## Scheduler

El scheduler corre con timezone America/Mexico_City y evita solapamientos por configuracion de misfire/coalesce.

Para uso continuo en Windows, puedes ejecutar en inicio de sesion con Task Scheduler.

## Logs y artefactos

- Logs: logs/factory.log (rotacion 10 MB, retencion 7 dias)
- Temporales por job: workspace/temp/
- Salida final: workspace/output/
- Revision manual: workspace/output/review_manual/

## Seguridad

- No subas secretos reales al repositorio.
- Usa .env local y comparte solo .env.example.

## Estado del proyecto

Repositorio activo con rama main y pipeline funcional V15 PRO para ejecucion local y VPS.
