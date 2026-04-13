"""Quality Gate — Pydantic validation + heuristic scoring.

Replaces the 350-line 'Procesar JSON' node from MASTER V13.
Validates AI output with Pydantic schema, then applies heuristic scoring.
Emits concrete ErrorCodes for each failure type.

MODULE CONTRACT:
  Input:  raw dict from AI + NichoConfig
  Output: (VideoContent | None, QualityScores with error_codes, error strings)
"""
from __future__ import annotations

import re
from typing import Optional

from loguru import logger
from pydantic import ValidationError

from config import app_config
from models.config_models import NichoConfig
from models.content import (
    BlockScores,
    ErrorCode,
    QualityScores,
    VideoContent,
)


def _script_word_range_for_platform(platform: str) -> tuple[int, int]:
    """Platform-aware target range for script length."""
    p = (platform or "").lower()
    if "facebook" in p:
        return 170, 260
    if "reel" in p or "instagram" in p:
        return 130, 200
    if "short" in p or "youtube" in p:
        return 120, 185
    if "tiktok" in p:
        return 130, 200
    return 120, 180


def validate_and_score(
    raw_data: dict,
    nicho: NichoConfig,
) -> tuple[Optional[VideoContent], QualityScores, list[str]]:
    """Validate AI output and compute quality scores.

    Returns:
        (content, scores, errors) — content is None if validation fails.
    """
    errors: list[str] = []

    # --- Step 1: Pydantic validation ---
    try:
        content = VideoContent(**raw_data)
    except ValidationError as e:
        import json
        logger.error(f"Validation Error raw data dump:\n{json.dumps(raw_data, ensure_ascii=False, indent=2)}")
        error_msgs = []
        for err in e.errors():
            field = ".".join(str(loc) for loc in err["loc"])
            msg = err["msg"]
            error_msgs.append(f"{field}: {msg}")
        errors.extend(error_msgs)
        return None, QualityScores(
            block_scores=BlockScores(),
            quality_score=0,
            quality_status="rechazado",
            error_codes=[ErrorCode.JSON_SCHEMA_INVALID],
        ), errors

    # --- Step 2: Heuristic scoring ---
    hook_h = _score_hook(content.gancho)
    desarrollo_h = _score_desarrollo(content.guion)
    cierre_h = _score_cierre(content.cta)

    # Combine model scores with heuristics (take the max)
    model_scores = content.block_scores
    final_scores = BlockScores(
        hook=max(model_scores.hook, content.hook_score, hook_h),
        desarrollo=max(model_scores.desarrollo, desarrollo_h),
        cierre=max(model_scores.cierre, cierre_h),
    )

    # Weighted quality score
    quality_score = round(
        final_scores.hook * 0.45
        + final_scores.desarrollo * 0.35
        + final_scores.cierre * 0.20,
        1,
    )

    # Approval check
    threshold = app_config.quality_threshold
    word_count = len(str(content.guion or "").split())
    min_words, max_words = _script_word_range_for_platform(nicho.plataforma)

    is_approved = (
        final_scores.hook >= 7
        and final_scores.desarrollo >= 7
        and final_scores.cierre >= 7
        and quality_score >= threshold
    )

    if word_count < min_words:
        is_approved = False
        errors.append(
            f"Guion demasiado corto para {nicho.plataforma}: {word_count} palabras; minimo recomendado {min_words}"
        )

    if word_count > max_words:
        # Not a hard failure, but keep traceability for prompt tuning.
        errors.append(
            f"Guion por encima del rango recomendado para {nicho.plataforma}: {word_count} palabras; recomendado <= {max_words}"
        )

    # Collect specific error codes for self-healer precision
    error_codes: list[ErrorCode] = []
    if final_scores.hook < 7:
        error_codes.append(ErrorCode.HOOK_TOO_WEAK)
    if final_scores.desarrollo < 7:
        error_codes.append(ErrorCode.DESARROLLO_WEAK)
    if final_scores.cierre < 7:
        error_codes.append(ErrorCode.CIERRE_WEAK)
    if quality_score < threshold:
        error_codes.append(ErrorCode.QUALITY_BELOW_THRESHOLD)
    if word_count < min_words:
        error_codes.append(ErrorCode.DESARROLLO_WEAK)

    quality = QualityScores(
        block_scores=final_scores,
        quality_score=quality_score,
        quality_status="aprobado" if is_approved else "rechazado",
        hook_heuristic=hook_h,
        desarrollo_heuristic=desarrollo_h,
        cierre_heuristic=cierre_h,
        error_codes=error_codes,
    )

    if not is_approved:
        errors.append(
            f"Calidad insuficiente: hook={final_scores.hook}, "
            f"desarrollo={final_scores.desarrollo}, "
            f"cierre={final_scores.cierre}, global={quality_score}"
        )

    logger.info(
        f"QA: {quality.quality_status} | "
        f"H={final_scores.hook} D={final_scores.desarrollo} C={final_scores.cierre} | "
        f"Global={quality_score} (threshold={threshold})"
    )

    return content, quality, errors


def _score_hook(gancho: str) -> float:
    """Heuristic: how strong is the hook?"""
    g = str(gancho or "")
    s = 0.0
    words = g.split()
    word_count = len(words)

    if 9 <= word_count <= 14:
        s += 4
    elif 6 <= word_count <= 18:
        s += 2

    if re.search(r"[0-9]", g):
        s += 2
    if re.search(r"[?¿]", g):
        s += 2
    if re.search(r"(secreto|nadie|viral|shock|explos|prohib|error|millon|trampa|mentira)", g, re.IGNORECASE):
        s += 2

    return min(10, max(1, s))


def _score_desarrollo(guion: str) -> float:
    """Heuristic: narrative quality of the script body."""
    g = str(guion or "")
    words = g.split()
    word_count = len(words)
    s = 0.0

    if 90 <= word_count <= 170:
        s += 4
    elif 60 <= word_count <= 200:
        s += 2

    sentences = re.split(r"[.!?]+", g)
    sentences = [s.strip() for s in sentences if s.strip()]
    avg_sentence = word_count / max(len(sentences), 1)
    if 6 <= avg_sentence <= 15:
        s += 3

    if re.search(r"(pero|sin embargo|mientras|entonces|resultado|finalmente)", g, re.IGNORECASE):
        s += 3

    return min(10, max(1, s))


def _score_cierre(cta: str) -> float:
    """Heuristic: strength of the call-to-action."""
    c = str(cta or "")
    s = 0.0

    if len(c) >= 12:
        s += 5
    if re.search(r"(guarda|comparte|sigue|comenta|pruebalo|hazlo hoy)", c, re.IGNORECASE):
        s += 3
    if re.search(r"(ahora|hoy|ya)", c, re.IGNORECASE):
        s += 2

    return min(10, max(1, s))
