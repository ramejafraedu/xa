# Video Factory V16 PRO

Fabrica automatizada de videos verticales (formato short) para "Faceless Channels" / "Cash Cow Channels". Pipeline multi-agente con verificación factual, sincronización audio-subtítulo, input manual de ideas, y gestión de memoria optimizada para servidores.

> **V15 → V16**: V16 agrega: (1) **Verification Agent** - verificación automática de datos/fuentes, (2) **Topic Injection** - input manual de ideas, (3) **Audio Sync** - sincronización WhisperX de subtítulos, (4) **Memory Manager** - control de RAM (20% max), (5) **Free APIs** - NumbersAPI, UselessFacts, Wikidata para hooks y verificación.

> **V14 → V15**: Pipeline multi-agente con director humano-en-el-loop.

---

## 🚀 Novedades V16.1 PRO

> **13 de abril 2026 — Video Factory V16.1 PRO**

### ✅ Mejoras implementadas en esta versión:

| # | Mejora | Archivo | Estado |
|---|--------|---------|--------|
| 1 | **Thumbnail Generator** con Gemini Imagen 3 | `tools/graphics/thumbnail_generator.py` | ✅ NUEVO |
| 2 | **Full EditingEngine ShortGPT** con JSON Markup inteligente | `tools/editing/EditingEngine.py` | ✅ NUEVO |
| 3 | **SaarD00 Composer PRO** — A/B split + avatar injection + xfade | `tools/video/composer_saar.py` | ✅ NUEVO |
| 4 | **Generador de Títulos/Descripciones/Hashtags** SEO automático | `agents/title_generator.py` | ✅ NUEVO |
| 5 | **LICENSE MIT** | `LICENSE` | ✅ NUEVO |
| 6 | **Demo videos + Screenshots** — guías y ejemplos | `README.md` (esta sección) | ✅ NUEVO |
| 7 | **Integración pipeline** — hooks en video_factory.py | `video_factory.py` (stage_render) | ✅ NUEVO |

### Detalles técnicos V16.1:

- **ThumbnailGeneratorTool**: genera thumbnails 9:16 con hook emocional vía LLM + Imagen 3. Auto-detecta nicho, construye prompt visual optimizado y guarda en `workspace/output/thumbnails/`.
- **FullEditingEngine**: reemplaza los hooks opcionales de ShortGPT con un motor completo. Genera `schema.json` con `visual_assets`, `audio_assets`, timing por capa, efecto por preset (cinematic/energetic/mystery) y transiciones inteligentes.
- **SaarComposerPRO**: 2 variantes por video (visual_1/visual_2), silence trim -50dB, volume boost configurable, xfade dinámico (fade/dissolve/wiperight), avatar overlay con chroma key opcional.
- **TitleGeneratorAgent**: Gemini Flash 2.5 con prompts especializados por nicho. 3 variantes por metadato. Pool curado de hashtags virales + generación LLM complementaria.

---

## 🎬 Demo — Prompts de Prueba

Usa estos 3 prompts en el pipeline para crear videos de demostración:

### Demo 1 — Curiosidades (Thumbnail + Metadata automáticos)
```bash
python video_factory.py --test curiosidades
# Genera: thumbnail 9:16 + título clickbait + 12 hashtags
```
**Prompt interno sugerido para el script:**
> *"5 datos sobre el cerebro humano que los científicos no esperaban descubrir. El #3 cambia todo lo que creías saber sobre la memoria."*

### Demo 2 — Misterio + SaarD00 A/B Split
```bash
python video_factory.py --v15 misterio
# Genera: variant_A.mp4 + variant_B.mp4 con diferente visual stock
```
**Prompt interno sugerido:**
> *"La historia oculta del FBI que el gobierno quiere que olvides. La conspiración más grande de los últimos 50 años explicada en 60 segundos."*

### Demo 3 — Thumbnail + FullEditingEngine Standalone
```python
# test_v161.py
from tools.graphics.thumbnail_generator import generate_thumbnail
from tools.editing.EditingEngine import build_editing_schema
from agents.title_generator import generate_metadata

# 1. Generar thumbnail
thumb = generate_thumbnail("El secreto que Newton ocultó", nicho="ciencia")
print(f"Thumbnail: {thumb['thumbnail_path']}")

# 2. Generar metadata
meta = generate_metadata("Guion sobre las leyes de Newton...", nicho="ciencia")
print(f"Título: {meta['titulo_recomendado']}")

# 3. Construir schema de edición
schema = build_editing_schema(
    scene_data=[{"visual_1": "clip1.mp4", "duration": 4.0}],
    fx_preset="cinematic",
    export_path="workspace/output/schema_test.json"
)
```

---

## 📸 Screenshots

> **Añadir aquí**: screenshots del dashboard, output de videos y thumbnails generados.
>
> **Cómo agregar screenshots al README:**
> 1. Guarda tus capturas en `docs/screenshots/` (crear la carpeta si no existe)
> 2. Usa la sintaxis: `![descripción](docs/screenshots/nombre.png)`
> 3. Para videos demo externos: `[![Ver demo](docs/screenshots/thumbnail_demo.png)](URL_youtube)`

**Estructura sugerida de screenshots:**
```
docs/
├── screenshots/
│   ├── dashboard_main.png       # Dashboard web en puerto 8000
│   ├── pipeline_running.png     # Pipeline V15 en ejecución
│   ├── thumbnail_example.png    # Ejemplo de thumbnail 9:16 generado
│   ├── ab_variants.png          # Variantes A y B del SaarComposer
│   └── title_generator.png      # Output del generador de metadatos
└── demos/
    └── demo_curiosidades.mp4    # Video demo renderizado
```

**Ejemplo de embed (cuando tengas el screenshot):**
```markdown
![Dashboard V16.1](docs/screenshots/dashboard_main.png)
```

---

## Caracteristicas V16

- **Verificación Factual**: Verifica automáticamente datos, universidades, términos psicológicos
- **Input Manual de Ideas**: Sistema de topic injection para temas específicos del usuario
- **Sincronización Audio-Subtítulo**: WhisperX forced alignment para timing preciso
- **Gestión de Memoria**: Límite de 20% RAM por video, streaming automático
- **APIs Gratuitas**: NumbersAPI, UselessFacts, Wikidata para hooks y verificación
- Pipeline multi-agente: Research → Script → Verification → Scene → Assets → Editor
- Checkpoints por etapa con aprobación manual opcional
- 5 nichos pre-configurados: finanzas, historia, curiosidades, historias_reddit, ia_herramientas
- TTS con fallback: ElevenLabs → Google Cloud TTS → Gemini → Edge TTS → Piper (offline)
- Video stock: Pexels, Pixabay, Coverr con rotación y caché
- Dashboard web con API REST
- Scheduler 24/7 con APScheduler
- Optimizado para Ubuntu Server (8GB RAM)

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

### Google TTS + Vertex (opcional)

Si quieres usar Google Cloud TTS en produccion:

1. Habilita APIs en GCP:
   - Generative Language API (Gemini)
   - Cloud Text-to-Speech API
2. Configura autenticacion:
   - Opcion A: `GOOGLE_TTS_API_KEY`
   - Opcion B: `GOOGLE_TTS_SERVICE_ACCOUNT_JSON` o `GOOGLE_APPLICATION_CREDENTIALS`
   - Opcion C: ADC con `gcloud auth application-default login`
3. Activa proveedor en `.env`:
   - `USE_GOOGLE_TTS=true`
4. Vertex se mantiene opt-in por defecto:
   - `USE_VERTEX_AI=false` (cambiar a `true` solo si ya tienes proyecto/ADC/IAM listos)

Variables de ajuste rapido:

- `GOOGLE_TTS_VOICE_NAME`
- `GOOGLE_TTS_LANGUAGE_CODE`
- `GOOGLE_TTS_SPEAKING_RATE`
- `GOOGLE_TTS_PITCH`

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

## Deployment en Ubuntu Server (Recomendado)

### Setup Automático

Para tu servidor DigitalOcean (Ubuntu 24.04, 8GB RAM):

```bash
# 1. Descargar el proyecto
git clone https://github.com/yourusername/video_factory.git
cd video_factory

# 2. Ejecutar script de setup
chmod +x setup_ubuntu.sh
./setup_ubuntu.sh

# 3. Configurar API keys
nano .env

# 4. Verificar instalación
./check_health.sh

# 5. Instalar servicio systemd
sudo cp /tmp/video-factory.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable video-factory
sudo systemctl start video-factory

# 6. Monitorear
sudo journalctl -u video-factory -f
```

### Configuración para 8GB RAM

El archivo `.env` ya viene pre-configurado con:

```bash
MAX_RAM_PERCENT_PER_JOB=20.0      # 20% = 1.6GB por video
ENABLE_MEMORY_STREAMING=true      # Usa disco si RAM baja
FRAME_BUFFER_SECONDS=30           # Buffer limitado
FORCE_GC_BETWEEN_STAGES=true      # Limpieza entre etapas
```

### Gestión del Servicio

```bash
# Ver estado
sudo systemctl status video-factory

# Ver logs
sudo journalctl -u video-factory -f

# Reiniciar
sudo systemctl restart video-factory

# Detener
sudo systemctl stop video-factory
```

### Autoarranque Dashboard al Encender (Linux/VPS)

Si quieres poder acceder al dashboard automaticamente despues de reiniciar la maquina:

```bash
cd ~/video_factory
chmod +x setup_ubuntu.sh install_autostart_services.sh
./setup_ubuntu.sh
./install_autostart_services.sh
```

Verifica:

```bash
sudo systemctl status video-factory-dashboard
sudo journalctl -u video-factory-dashboard -f
```

Notas:

- El dashboard usa `DASHBOARD_HOST` y `DASHBOARD_PORT` desde `.env`.
- Valor recomendado para acceso remoto: `DASHBOARD_HOST=0.0.0.0`.
- Asegura regla de firewall para el puerto del dashboard (default 8000).

### Limpieza Automática

Programar en cron:

```bash
crontab -e
# Agregar:
0 3 * * * /home/xavito/video_factory/cleanup.sh
```

## Scheduler

El scheduler corre con timezone America/Mexico_City y evita solapamientos por configuracion de misfire/coalesce.

Para Windows: ejecutar en inicio de sesion con Task Scheduler.
Para Ubuntu: usar systemd (ver arriba).

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
