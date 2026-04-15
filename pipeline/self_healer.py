"""Unified Self-Healer — handles ALL pipeline failures.

4 healing strategies using Gemini first and GPT fallback:
  - fix_prompt: Re-generate content with correction instructions
  - fix_json: Fix malformed JSON from AI
  - fix_audio: Retry TTS with different params
  - fix_render: Adjust FFmpeg params and retry

Each attempt gets a concrete ErrorCode for precise diagnosis.
Max 2 healing attempts per failure type per job.

MODULE CONTRACT:
  Input:  PipelineResult + FailureType + ErrorCode + error details
  Output: corrected string (JSON, params) or None
"""
from __future__ import annotations

import json
import re
from typing import Optional

from loguru import logger

from config import settings, app_config
from models.config_models import NichoConfig
from models.content import ErrorCode, FailureType, HealingRecord, PipelineResult
from services.llm_router import call_llm_primary_gemini


_last_healing_model_used = ""


def attempt_healing(
    result: PipelineResult,
    failure_type: FailureType,
    stage: str,
    error_message: str,
    original_input: str = "",
    nicho: Optional[NichoConfig] = None,
    error_code: ErrorCode = ErrorCode.UNKNOWN,
) -> Optional[str]:
    """Attempt to heal a pipeline failure.

    Args:
        result: Current pipeline result (for tracking).
        failure_type: Type of failure to heal.
        stage: Pipeline stage where failure occurred.
        error_message: Error details.
        original_input: The original content/params that failed.
        nicho: Niche config (needed for fix_prompt).
        error_code: Specific error code for precise diagnosis.

    Returns:
        Corrected output string, or None if healing failed.
    """
    # Check retry limit
    type_attempts = sum(
        1 for h in result.healing_attempts if h.failure_type == failure_type
    )
    if type_attempts >= app_config.max_healing_attempts:
        logger.warning(
            f"Max healing attempts ({app_config.max_healing_attempts}) reached for {failure_type.value}"
        )
        return None

    logger.info(f"🔄 Self-healing: {failure_type.value} (attempt {type_attempts + 1})")

    record = HealingRecord(
        attempt=type_attempts + 1,
        failure_type=failure_type,
        error_code=error_code,
        stage=stage,
        error_message=error_message,
        original_input=original_input[:500],
    )

    try:
        corrected = _dispatch_healing(failure_type, error_message, original_input, nicho, error_code)
        if corrected:
            record.success = True
            record.corrected_output = corrected[:500]
            record.model_used = _last_healing_model_used or settings.inference_model
            result.healing_attempts.append(record)
            logger.info(f"✅ Healing succeeded for {failure_type.value}")
            return corrected

    except Exception as e:
        logger.error(f"Healing error: {e}")
        record.error_message += f" | Healing error: {e}"

    record.success = False
    result.healing_attempts.append(record)
    return None


def _dispatch_healing(
    failure_type: FailureType,
    error_message: str,
    original_input: str,
    nicho: Optional[NichoConfig],
    error_code: ErrorCode = ErrorCode.UNKNOWN,
) -> Optional[str]:
    """Route to the correct healing strategy."""
    if failure_type == FailureType.PROMPT:
        return _fix_prompt(error_message, original_input, nicho, error_code)
    elif failure_type == FailureType.JSON:
        return _fix_json(error_message, original_input)
    elif failure_type == FailureType.AUDIO:
        return _fix_audio(error_message, error_code)
    elif failure_type == FailureType.RENDER:
        return _fix_render(error_message, original_input, error_code)
    return None


def _call_ai(system_prompt: str, user_prompt: str) -> str:
    """Call healing LLM with Gemini primary and GPT fallback."""
    global _last_healing_model_used

    text, model_used = call_llm_primary_gemini(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        temperature=0.5,
        timeout=45,
        max_retries=2,
        purpose="self_healer",
    )

    if not text:
        raise RuntimeError("AI healing call failed in Gemini+GPT path")

    _last_healing_model_used = model_used or ""
    return text


# ---------------------------------------------------------------------------
# Strategy 1: Fix Prompt (content quality too low)
# ---------------------------------------------------------------------------
def _fix_prompt(error_message: str, original_input: str, nicho: Optional[NichoConfig], error_code: ErrorCode = ErrorCode.UNKNOWN) -> Optional[str]:
    """Re-generate content with specific correction instructions.

    Uses the concrete ErrorCode to give the AI a precise diagnosis.
    """
    # Map error codes to precise correction instructions
    code_instructions = {
        ErrorCode.HOOK_TOO_WEAK: "El hook/gancho es debil. Necesita mas polarizacion, un dato extremo, o una pregunta que genere urgencia.",
        ErrorCode.DESARROLLO_WEAK: "El desarrollo del guion es plano. Necesita mas cliffhangers, tension narrativa, y frases cortas.",
        ErrorCode.CIERRE_WEAK: "El CTA es debil. Necesita una llamada a la accion mas emotiva y concreta.",
        ErrorCode.QUALITY_BELOW_THRESHOLD: "La calidad global esta por debajo del umbral. Mejora hook, narrativa y cierre simultaneamente.",
    }
    specific_instruction = code_instructions.get(error_code, "Mejora la calidad general del contenido.")

    system = f"""Eres un editor viral experto. El guion anterior no paso el control de calidad.
DIAGNOSTICO PRECISO: {error_code.value}
INSTRUCCION ESPECIFICA: {specific_instruction}

REGLAS:
- Hook en <=1.8 segundos con polarizacion real
- Frases de 5-12 palabras
- Cliffhangers cada 8-10 segundos
- 2-4 muletillas humanas
- PROHIBIDO comillas dobles en textos
- Devuelve SOLO JSON valido, sin texto extra"""

    user = f"""El guion anterior fallo en calidad:
CODIGO ERROR: {error_code.value}
ERRORES: {error_message}

GUION ORIGINAL:
{original_input[:1000]}

NICHO: {nicho.nombre if nicho else 'general'}
TONO: {nicho.tono if nicho else 'neutro'}

Corrige las debilidades especificas y devuelve el JSON completo mejorado con la misma estructura."""

    return _call_ai(system, user)


# ---------------------------------------------------------------------------
# Strategy 2: Fix JSON (malformed AI response)
# ---------------------------------------------------------------------------
def _fix_json(error_message: str, malformed_json: str) -> Optional[str]:
    """Fix a malformed JSON response from the AI."""
    system = """Eres un experto en reparar JSON malformado. 
Se te da un JSON roto y el error de parseo.
Arreglalo y devuelve SOLO el JSON corregido, sin explicacion."""

    user = f"""ERROR: {error_message}

JSON ROTO:
{malformed_json[:2000]}

Devuelve SOLO el JSON reparado."""

    result = _call_ai(system, user)
    if result:
        # Validate it's actually valid JSON
        cleaned = re.sub(r"```json\s*", "", result, flags=re.IGNORECASE)
        cleaned = re.sub(r"```\s*", "", cleaned).strip()
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end > start:
            try:
                json.loads(cleaned[start : end + 1])
                return cleaned[start : end + 1]
            except json.JSONDecodeError:
                pass
    return None


# ---------------------------------------------------------------------------
# Strategy 3: Fix Audio (TTS failure)
# ---------------------------------------------------------------------------
def _fix_audio(error_message: str, error_code: ErrorCode = ErrorCode.UNKNOWN) -> Optional[str]:
    """Return corrected TTS parameters as JSON string."""
    # Most audio fixes don't need AI — they're parameter adjustments
    code_fixes = {
        ErrorCode.TTS_EMPTY_AUDIO: json.dumps({"action": "retry_edge_tts"}),
        ErrorCode.TTS_GEMINI_FAIL: json.dumps({"action": "retry_edge_tts"}),
        ErrorCode.TTS_EDGE_FAIL: json.dumps({"action": "retry_edge_tts", "change_voice": True}),
        ErrorCode.TTS_FILTER_FAIL: json.dumps({"action": "retry_with_simpler_filters"}),
    }

    # Check error_code first (most precise)
    if error_code in code_fixes:
        logger.info(f"Audio fix by error_code: {error_code.value}")
        return code_fixes[error_code]

    # Fallback: pattern matching on error message
    fixes = {
        "NO_AUDIO": json.dumps({"action": "retry_edge_tts"}),
        "WAV vacio": json.dumps({"action": "retry_edge_tts"}),
        "HTTP 429": json.dumps({"action": "wait_and_retry", "wait_seconds": 30}),
        "HTTP 4": json.dumps({"action": "retry_edge_tts"}),
        "HTTP 5": json.dumps({"action": "wait_and_retry", "wait_seconds": 10}),
        "ffmpeg": json.dumps({"action": "retry_with_simpler_filters"}),
    }

    for pattern, fix in fixes.items():
        if pattern.lower() in error_message.lower():
            logger.info(f"Audio fix matched pattern '{pattern}'")
            return fix

    return json.dumps({"action": "retry_edge_tts"})


# ---------------------------------------------------------------------------
# Strategy 4: Fix Render (FFmpeg failure)
# ---------------------------------------------------------------------------
def _fix_render(error_message: str, original_params: str, error_code: ErrorCode = ErrorCode.UNKNOWN) -> Optional[str]:
    """Analyze FFmpeg error and return corrected parameters.

    Uses error_code for deterministic fixes when possible,
    falls back to AI analysis for unknown errors.
    """
    # Deterministic fixes for known error codes
    deterministic = {
        ErrorCode.FFMPEG_TIMEOUT: json.dumps({
            "remove_filters": ["zoompan"],
            "change_preset": "ultrafast",
            "reduce_resolution": False,
            "add_flags": ["-max_muxing_queue_size", "4096"],
        }),
        ErrorCode.FFMPEG_CONCAT_FAIL: json.dumps({
            "remove_filters": [],
            "change_preset": "fast",
            "reduce_resolution": False,
            "add_flags": ["-safe", "0", "-max_muxing_queue_size", "2048"],
        }),
        ErrorCode.FFMPEG_AUDIO_MIX_FAIL: json.dumps({
            "remove_filters": [],
            "change_preset": "veryfast",
            "skip_music": True,
            "add_flags": [],
        }),
        ErrorCode.GREENSCREEN_DETECTED: json.dumps({
            "action": "drop_greenscreen_clips",
            "rebuild_timeline": True,
            "retry_pre_render_validation": True,
        }),
    }
    if error_code in deterministic:
        logger.info(f"Render fix by error_code: {error_code.value}")
        return deterministic[error_code]

    # Special-case: Remotion compositor frame_cache panic -> return recovery plan
    lowered = (error_message or "").lower()
    if (
        "frame_cache.rs" in lowered
        or "thread '<unnamed>' panicked" in lowered
        or "called `option::unwrap()` on a `none` value" in lowered
        or "compositor exited with code 1" in lowered
    ):
        logger.info("Render fix: detected Remotion compositor frame_cache panic; returning recovery plan")
        plan = {
            "action": "remotion_frame_cache_recovery",
            "clear_cache": True,
            "rebuild_bundle": True,
            "increase_memory_mb": int(getattr(settings, "remotion_compositor_memory_limit", 8192) or 8192),
            "max_retries": int(getattr(settings, "remotion_frame_cache_max_retries", 2) or 2),
            "force_fallback_if_persistent": bool(getattr(settings, "remotion_frame_cache_force_fallback", False)),
        }
        return json.dumps(plan)

    # AI analysis for unknown errors
    system = """Eres un ingeniero experto en FFmpeg y Python.
El sistema arrojo el siguiente error al intentar renderizar un video.
Analiza el error_log y devuelve un JSON con los parametros corregidos.
NO devuelvas explicacion, SOLO JSON con las correcciones."""

    user = f"""ERROR DE FFMPEG:
{error_message[:1500]}

PARAMETROS ORIGINALES:
{original_params[:1000]}

Devuelve JSON con correcciones, ejemplo:
{{"remove_filters": ["zoompan"], "change_preset": "fast", "reduce_resolution": false, "add_flags": ["-max_muxing_queue_size", "2048"]}}"""

    result = _call_ai(system, user)
    if result:
        cleaned = re.sub(r"```json\s*", "", result, flags=re.IGNORECASE)
        cleaned = re.sub(r"```\s*", "", cleaned).strip()
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end > start:
            try:
                json.loads(cleaned[start : end + 1])
                return cleaned[start : end + 1]
            except json.JSONDecodeError:
                pass

    # Fallback: return safe defaults
    return json.dumps({
        "remove_filters": ["zoompan"],
        "change_preset": "fast",
        "reduce_resolution": False,
        "add_flags": ["-max_muxing_queue_size", "2048"],
    })
