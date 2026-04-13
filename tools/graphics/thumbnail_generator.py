"""Generador de Thumbnails con IA — Video Factory V16.1 PRO.

Genera thumbnails de alta retención para YouTube Shorts y TikTok.
- Formato: 1080×1920 (9:16 vertical, imagen nativa de Imagen 3)
- Texto bold con alto contraste y hook emocional vía Gemini Flash
- Guarda en workspace/output/thumbnails/
- Compatible con tool_registry autodiscovery
"""

from __future__ import annotations

import base64
import os
import time
from pathlib import Path
from typing import Any

from loguru import logger

from tools.base_tool import (
    BaseTool,
    Determinism,
    ExecutionMode,
    ResourceProfile,
    RetryPolicy,
    ToolResult,
    ToolRuntime,
    ToolStability,
    ToolStatus,
    ToolTier,
)

# ─────────────────────────────────────────────
# Plantillas de prompt por nicho
# ─────────────────────────────────────────────
_NICHO_TEMPLATES: dict[str, str] = {
    "curiosidades": (
        "eye-catching thumbnail about '{titulo}', shocked person face, bold white text "
        "with black outline at top: '{hook}', vibrant colors, dramatic lighting, "
        "ultra-realistic, cinematic 9:16 vertical"
    ),
    "misterio": (
        "dark mysterious thumbnail for '{titulo}', dark tones, fog effect, "
        "glowing eyes, bold yellow text at top: '{hook}', thriller atmosphere, 9:16"
    ),
    "motivacion": (
        "motivational thumbnail '{titulo}', powerful person silhouette at sunset, "
        "golden gradient, bold white text: '{hook}', epic cinematic, 9:16 vertical"
    ),
    "historia": (
        "historical thumbnail '{titulo}', vintage sepia effect, newspaper overlay, "
        "bold red text: '{hook}', dramatic, aged paper texture, 9:16"
    ),
    "ciencia": (
        "science thumbnail '{titulo}', futuristic neon blue background, hologram effects, "
        "bold cyan text: '{hook}', high-tech laboratory, 9:16"
    ),
    "default": (
        "YouTube Shorts thumbnail for '{titulo}', bold large text: '{hook}', "
        "high contrast background, emotional impact, trending style, 9:16 vertical aspect ratio, "
        "professional design, vibrant colors, photorealistic"
    ),
}

_HOOK_DEFAULTS: list[str] = [
    "¿Lo sabías?",
    "INCREÍBLE",
    "NADIE TE LO DICE",
    "ESTO CAMBIA TODO",
    "NO LO VAS A CREER",
    "¿POR QUÉ?",
]


def _generar_hook_llm(titulo: str, nicho: str, api_key: str) -> str:
    """Genera un hook emocional usando Gemini Flash 2.5."""
    try:
        import google.generativeai as genai  # type: ignore

        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-2.5-flash")
        prompt = (
            f"Eres experto en YouTube Shorts. Genera UN solo hook de máximo 5 palabras "
            f"en español (ALL CAPS, impactante, clickbait) para el video: '{titulo}' "
            f"de la categoría '{nicho}'. Solo devuelve el hook, sin comillas ni explicación."
        )
        resp = model.generate_content(prompt)
        hook = resp.text.strip().upper().split("\n")[0][:40]
        return hook
    except Exception as e:
        logger.warning(f"ThumbnailGenerator: fallback hook — {e}")
        import random
        return random.choice(_HOOK_DEFAULTS)


def _build_imagen_prompt(titulo: str, nicho: str, hook: str) -> str:
    """Construye el prompt optimizado para Imagen 3."""
    template = _NICHO_TEMPLATES.get(nicho.lower(), _NICHO_TEMPLATES["default"])
    return template.format(titulo=titulo[:60], hook=hook[:35])


def _call_imagen_api(
    prompt: str,
    api_key: str,
    model: str,
    output_path: Path,
) -> bool:
    """Llama a la API de generación de imágenes con soporte para Vertex AI y Gemini standard.

    Prueba primero Vertex AI (si está configurado), luego el endpoint estándar de Gemini,
    y finalmente la API de Imagen directa como fallback.

    Returns:
        True si la imagen se guardó correctamente, False en caso de error.
    """
    import base64
    import os
    import requests

    # ── Opción 1: Vertex AI (si está configurado)
    use_vertex = os.environ.get("USE_VERTEX_AI", "").lower() in ("true", "1", "yes")
    project_id = os.environ.get("VERTEX_PROJECT_ID", "")
    location = os.environ.get("VERTEX_LOCATION", "us-central1")
    credentials_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")

    if use_vertex and project_id and credentials_path:
        try:
            import google.auth  # type: ignore
            import google.auth.transport.requests  # type: ignore

            creds, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            auth_req = google.auth.transport.requests.Request()
            creds.refresh(auth_req)
            token = creds.token

            # imagegeneration no funciona en location=global, usar us-central1
            vertex_location = location if location != "global" else "us-central1"

            # Modelo Imagen en Vertex AI
            vertex_model = "imagegeneration@006"  # Imagen 3 en Vertex
            vertex_url = (
                f"https://{vertex_location}-aiplatform.googleapis.com/v1/"
                f"projects/{project_id}/locations/{vertex_location}/"
                f"publishers/google/models/{vertex_model}:predict"
            )


            resp = requests.post(
                vertex_url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json={
                    "instances": [{"prompt": prompt}],
                    "parameters": {
                        "sampleCount": 1,
                        "aspectRatio": "9:16",
                    },
                },
                timeout=120,
            )
            if resp.status_code == 200:
                data = resp.json()
                predictions = data.get("predictions", [])
                if predictions:
                    b64 = (
                        predictions[0].get("bytesBase64Encoded")
                        or predictions[0].get("image", {}).get("imageBytes", "")
                    )
                    if b64:
                        output_path.write_bytes(base64.b64decode(b64))
                        logger.info(f"ThumbnailGenerator: imagen generada vía Vertex AI → {output_path.name}")
                        return True
            logger.debug(f"ThumbnailGenerator: Vertex AI respondió {resp.status_code}, probando fallback")
        except Exception as ve:
            logger.debug(f"ThumbnailGenerator: Vertex AI no disponible ({ve}), usando Gemini standard")

    # ── Opción 2: Gemini generateContent con inline image response
    # (gemini-2.0-flash-preview-image-generation)
    try:
        gemini_image_models = [
            "gemini-2.0-flash-preview-image-generation",
            "gemini-3-flash-image",
        ]
        genai_url_base = "https://generativelanguage.googleapis.com/v1beta/models"

        for gmodel in gemini_image_models:
            try:
                resp = requests.post(
                    f"{genai_url_base}/{gmodel}:generateContent",
                    headers={
                        "Content-Type": "application/json",
                        "x-goog-api-key": api_key,
                    },
                    json={
                        "contents": [{"parts": [{"text": prompt}]}],
                        "generationConfig": {"responseModalities": ["IMAGE"]},
                    },
                    timeout=120,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    candidates = data.get("candidates", [])
                    for cand in candidates:
                        for part in cand.get("content", {}).get("parts", []):
                            inline = part.get("inlineData", {})
                            if inline.get("data"):
                                output_path.write_bytes(base64.b64decode(inline["data"]))
                                logger.info(f"ThumbnailGenerator: imagen vía {gmodel} → {output_path.name}")
                                return True
            except Exception:
                continue
    except Exception as ge:
        logger.debug(f"ThumbnailGenerator: Gemini generateContent falló — {ge}")

    # ── Opción 3: Imagen predict endpoint (legacy)
    try:
        resp = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:predict",
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": api_key,
            },
            json={
                "instances": [{"prompt": prompt}],
                "parameters": {"sampleCount": 1, "aspectRatio": "9:16"},
            },
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()
        predictions = data.get("predictions", [])
        if predictions:
            b64 = predictions[0].get("bytesBase64Encoded", "")
            if b64:
                output_path.write_bytes(base64.b64decode(b64))
                return True
    except Exception as pe:
        logger.debug(f"ThumbnailGenerator: predict endpoint falló — {pe}")

    return False



class ThumbnailGeneratorTool(BaseTool):
    """Genera thumbnails 9:16 para YouTube Shorts usando Gemini Imagen 3."""

    name = "thumbnail_generator"
    version = "1.0.0"
    tier = ToolTier.GENERATE
    capability = "thumbnail_generation"
    provider = "google_imagen"
    stability = ToolStability.BETA
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.STOCHASTIC
    runtime = ToolRuntime.API

    dependencies = []  # API key verificada dinámicamente
    install_instructions = (
        "Configura GOOGLE_API_KEY o GEMINI_API_KEY en el archivo .env.\n"
        "  → https://aistudio.google.com/apikey"
    )

    capabilities = [
        "generate_thumbnail",
        "generate_cover_image",
        "text_to_thumbnail",
        "shorts_thumbnail",
    ]
    best_for = [
        "YouTube Shorts thumbnails 9:16",
        "TikTok covers con texto bold",
        "Thumbnails de alta retención con hook emocional",
    ]
    not_good_for = [
        "thumbnails 16:9 (landscape)",
        "animaciones o GIFs",
    ]

    input_schema = {
        "type": "object",
        "required": ["titulo"],
        "properties": {
            "titulo": {
                "type": "string",
                "description": "Título del video para el que se genera el thumbnail",
            },
            "nicho": {
                "type": "string",
                "default": "default",
                "description": "Nicho del video: curiosidades, misterio, motivacion, historia, ciencia, default",
            },
            "hook": {
                "type": "string",
                "description": "Texto de hook para el thumbnail (opcional; se genera con LLM si no se proporciona)",
            },
            "output_path": {
                "type": "string",
                "description": "Ruta del archivo de salida. Por defecto: workspace/output/thumbnails/<titulo>.png",
            },
            "model": {
                "type": "string",
                "default": "gemini-3-flash-image",
                "description": "Modelo de Imagen a usar",
            },
            "auto_hook": {
                "type": "boolean",
                "default": True,
                "description": "Generar hook con LLM si no se proporciona",
            },
        },
    }

    output_schema = {
        "type": "object",
        "properties": {
            "thumbnail_path": {"type": "string"},
            "hook_used": {"type": "string"},
            "prompt_used": {"type": "string"},
            "model": {"type": "string"},
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=1, ram_mb=512, vram_mb=0, disk_mb=200, network_required=True
    )
    retry_policy = RetryPolicy(max_retries=2, retryable_errors=["rate_limit", "timeout"])
    idempotency_key_fields = ["titulo", "nicho", "hook"]
    side_effects = [
        "escribe imagen PNG en output_path",
        "llama a Google Generative AI API",
    ]
    user_visible_verification = [
        "Verifica que el thumbnail tenga texto legible",
        "Confirma relación de aspecto 9:16 vertical",
        "Comprueba que el hook sea visible e impactante",
    ]

    def _get_api_key(self) -> str | None:
        return os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE if self._get_api_key() else ToolStatus.UNAVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        model = inputs.get("model", "gemini-3-flash-image")
        if "ultra" in model:
            return 0.08
        if "flash" in model:
            return 0.03
        return 0.04

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        api_key = self._get_api_key()
        if not api_key:
            return ToolResult(
                success=False,
                error="No se encontró GOOGLE_API_KEY. " + self.install_instructions,
            )

        start = time.time()
        titulo = inputs["titulo"]
        nicho = inputs.get("nicho", "default")
        model = inputs.get("model", "gemini-3-flash-image")
        auto_hook = inputs.get("auto_hook", True)

        # ── 1. Generar o usar hook ──
        hook = inputs.get("hook", "")
        if not hook and auto_hook:
            logger.info(f"ThumbnailGenerator: generando hook LLM para '{titulo}'")
            hook = _generar_hook_llm(titulo, nicho, api_key)
        if not hook:
            import random
            hook = random.choice(_HOOK_DEFAULTS)

        logger.info(f"ThumbnailGenerator: hook='{hook}' | nicho={nicho}")

        # ── 2. Construir prompt visual ──
        imagen_prompt = _build_imagen_prompt(titulo, nicho, hook)
        logger.debug(f"ThumbnailGenerator: prompt='{imagen_prompt[:120]}...'")

        # ── 3. Determinar ruta de salida ──
        if "output_path" in inputs:
            output_path = Path(inputs["output_path"])
        else:
            # Buscar raíz del workspace
            base = Path(__file__).resolve().parent.parent.parent
            thumb_dir = base / "workspace" / "output" / "thumbnails"
            safe_name = "".join(c if c.isalnum() or c in "._- " else "_" for c in titulo)
            safe_name = safe_name[:60].strip()
            ts = int(time.time())
            output_path = thumb_dir / f"{safe_name}_{ts}.png"

        output_path.parent.mkdir(parents=True, exist_ok=True)

        # ── 4. Llamar a la API de Imagen (Vertex AI → Gemini → predict) ──
        ok = _call_imagen_api(
            prompt=imagen_prompt,
            api_key=api_key,
            model=model,
            output_path=output_path,
        )

        if not ok or not output_path.exists():
            logger.error("ThumbnailGenerator: ningún endpoint de imagen respondió correctamente")
            return ToolResult(
                success=False,
                error=(
                    "No se pudo generar el thumbnail. "
                    "Verifica: USE_VERTEX_AI, GOOGLE_APPLICATION_CREDENTIALS o GEMINI_API_KEY en .env"
                ),
            )

        duration = round(time.time() - start, 2)
        logger.success(
            f"ThumbnailGenerator: thumbnail guardado en {output_path} ({duration}s)"
        )

        return ToolResult(
            success=True,
            data={
                "thumbnail_path": str(output_path),
                "hook_used": hook,
                "prompt_used": imagen_prompt,
                "model": model,
                "nicho": nicho,
                "titulo": titulo,
                "size_bytes": output_path.stat().st_size,
            },
            artifacts=[str(output_path)],
            cost_usd=self.estimate_cost(inputs),
            duration_seconds=duration,
            model=model,
        )



# ─────────────────────────────────────────────
# Helper standalone — para llamar desde pipeline
# ─────────────────────────────────────────────

def generate_thumbnail(
    titulo: str,
    nicho: str = "default",
    hook: str = "",
    output_path: str | None = None,
    model: str = "gemini-3-flash-image",
) -> dict[str, Any]:
    """Interfaz simplificada para el pipeline.

    Uso desde pipeline_v15.py::

        from tools.graphics.thumbnail_generator import generate_thumbnail
        resultado = generate_thumbnail("Los secretos del FBI", nicho="misterio")
        thumbnail_path = resultado["thumbnail_path"]
    """
    tool = ThumbnailGeneratorTool()
    inputs: dict[str, Any] = {
        "titulo": titulo,
        "nicho": nicho,
        "model": model,
        "auto_hook": True,
    }
    if hook:
        inputs["hook"] = hook
    if output_path:
        inputs["output_path"] = output_path

    result = tool.execute(inputs)
    if result.success:
        return result.data
    raise RuntimeError(f"ThumbnailGenerator falló: {result.error}")
