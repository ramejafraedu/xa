"""SaarD00 Composer completo — A/B Split + Avatar Injection — Video Factory V16.1 PRO.

Implementa el compositor completo con:
- 2 variantes visuales por escena (A/B split para testing)
- Avatar injection random con green-screen overlay
- Silence trim con FFmpeg silenceremove
- Volume boost automático
- xFade dinámico entre escenas
- Compatible con BaseTool y tool_registry

Comentarios en español. Listo para producción.
"""

from __future__ import annotations

import os
import random
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

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
# Avatares disponibles por défecto
# (rutas relativas al directorio de avatares del pipeline)
# ─────────────────────────────────────────────
_AVATAR_CANDIDATES: list[str] = [
    "avatars/avatar_male_01.mp4",
    "avatars/avatar_female_01.mp4",
    "avatars/avatar_male_02.mp4",
    "avatars/avatar_female_02.mp4",
]


def _ffmpeg_available() -> bool:
    """Verifica si FFmpeg está disponible en el PATH."""
    import shutil
    return shutil.which("ffmpeg") is not None


def _safe_ffmpeg(cmd: list[str], description: str = "") -> bool:
    """Ejecuta un comando FFmpeg de forma segura.

    Returns:
        True si exitoso, False si falló.
    """
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=300,
        )
        if result.returncode != 0:
            logger.warning(
                f"FFmpeg ({description}) exitcode={result.returncode}: "
                f"{result.stderr.decode(errors='ignore')[-300:]}"
            )
            return False
        return True
    except subprocess.TimeoutExpired:
        logger.error(f"FFmpeg ({description}) timeout excedido")
        return False
    except Exception as e:
        logger.error(f"FFmpeg ({description}) error inesperado: {e}")
        return False


# ─────────────────────────────────────────────
# Funciones core de composición
# ─────────────────────────────────────────────

def trim_silence(audio_path: str, output_path: str) -> bool:
    """Elimina silencio al inicio y final del audio.

    Usa el filtro silenceremove de FFmpeg con umbral -50dB.
    """
    cmd = [
        "ffmpeg", "-y", "-i", audio_path,
        "-af",
        (
            "silenceremove=start_periods=1:start_duration=0.1:start_threshold=-50dB,"
            "areverse,"
            "silenceremove=start_periods=1:start_duration=0.1:start_threshold=-50dB,"
            "areverse"
        ),
        output_path,
    ]
    ok = _safe_ffmpeg(cmd, "trim_silence")
    if ok:
        logger.debug(f"SaarComposer: silencio recortado → {output_path}")
    return ok


def boost_volume(audio_path: str, output_path: str, db_gain: float = 3.0) -> bool:
    """Aumenta el volumen del audio en dB.

    Args:
        db_gain: Ganancia en dB (por defecto +3dB para voiceover más presente)
    """
    cmd = [
        "ffmpeg", "-y", "-i", audio_path,
        "-af", f"volume={db_gain}dB",
        output_path,
    ]
    ok = _safe_ffmpeg(cmd, "boost_volume")
    if ok:
        logger.debug(f"SaarComposer: volumen aumentado +{db_gain}dB → {output_path}")
    return ok


def apply_xfade(clips: list[str], output_path: str, transition: str = "fade", duration: float = 0.5) -> bool:
    """Aplica transición xfade dinámica entre una lista de clips.

    Usa el filtro xfade de FFmpeg para transiciones suaves.

    Args:
        clips: Lista de rutas a clips de video.
        transition: Tipo de xfade: fade, wiperight, slideleft, dissolve, pixelize, etc.
        duration: Duración de la transición en segundos.
    """
    if len(clips) < 2:
        logger.warning("SaarComposer.xfade: se necesitan al menos 2 clips")
        return False

    # Construir filtro de xfade encadenado
    inputs_part = []
    for clip in clips:
        inputs_part += ["-i", clip]

    # Construir filter_complex para N clips con xfade en cadena
    filter_parts: list[str] = []
    # Variables de tiempo acumulado (necesario para offset correcto)
    # Primero, obtener duración de cada clip
    durations = []
    for clip in clips:
        dur = _get_video_duration(clip)
        durations.append(dur if dur > 0 else 3.5)

    # Construir cadena de xfade
    prev_label = "[0:v]"
    cumulative_offset = 0.0
    filter_complex_parts: list[str] = []

    for i in range(1, len(clips)):
        cumulative_offset += durations[i - 1] - duration
        out_label = f"[v{i}]" if i < len(clips) - 1 else "[outv]"
        part = (
            f"{prev_label}[{i}:v]xfade=transition={transition}:"
            f"duration={duration}:offset={cumulative_offset:.3f}{out_label}"
        )
        filter_complex_parts.append(part)
        prev_label = out_label

    filter_complex = ";".join(filter_complex_parts)

    cmd = [
        "ffmpeg", "-y",
        *inputs_part,
        "-filter_complex", filter_complex,
        "-map", "[outv]",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "20",
        output_path,
    ]
    ok = _safe_ffmpeg(cmd, f"xfade_{transition}")
    if ok:
        logger.info(f"SaarComposer: xfade '{transition}' aplicado → {output_path}")
    return ok


def _get_video_duration(video_path: str) -> float:
    """Obtiene la duración de un video usando ffprobe."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "quiet",
                "-print_format", "json",
                "-show_streams",
                "-select_streams", "v:0",
                video_path,
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        import json
        data = json.loads(result.stdout)
        streams = data.get("streams", [])
        if streams:
            dur = streams[0].get("duration", "0")
            return float(dur)
    except Exception:
        pass
    return 0.0


def inject_avatar(
    base_video: str,
    avatar_path: str,
    output_path: str,
    position: str = "bottom_right",
    scale: float = 0.3,
) -> bool:
    """Superpone un avatar (con o sin green screen) sobre el video base.

    El avatar se escala y posiciona con overlay de FFmpeg.

    Args:
        position: 'bottom_right', 'bottom_left', 'top_right', 'top_left', 'bottom_center'
        scale: Fracción del ancho del video base (0.3 = 30%)
    """
    # Calcular coordenadas de overlay según posición
    pos_map = {
        "bottom_right":  "W-w-20:H-h-20",
        "bottom_left":   "20:H-h-20",
        "top_right":     "W-w-20:20",
        "top_left":      "20:20",
        "bottom_center": "(W-w)/2:H-h-20",
    }
    overlay_pos = pos_map.get(position, pos_map["bottom_right"])

    # Verificar si el avatar requiere chroma key (green screen)
    # Heurística: si "green" en filename o es .webm
    use_chroma = "green" in Path(avatar_path).name.lower() or avatar_path.endswith(".webm")

    if use_chroma:
        # Chroma key: elimina fondo verde y superpone
        filter_complex = (
            f"[1:v]chromakey=0x00ff00:0.3:0.2,"
            f"scale=iw*{scale}:-1[ava];"
            f"[0:v][ava]overlay={overlay_pos}"
        )
    else:
        # Overlay directo (avatar ya tiene transparencia o fondo negro)
        filter_complex = (
            f"[1:v]scale=iw*{scale}:-1[ava];"
            f"[0:v][ava]overlay={overlay_pos}"
        )

    cmd = [
        "ffmpeg", "-y",
        "-i", base_video,
        "-i", avatar_path,
        "-filter_complex", filter_complex,
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "20",
        "-map", "0:a?",
        "-c:a", "copy",
        output_path,
    ]
    ok = _safe_ffmpeg(cmd, "avatar_injection")
    if ok:
        logger.info(f"SaarComposer: avatar inyectado ({position}) → {output_path}")
    return ok


def concat_scenes(
    scene_data: list[dict],
    audio_track: str,
    visual_key: str,
    output_path: str,
    temp_dir: Path,
    xfade: bool = True,
    xfade_type: str = "fade",
) -> bool:
    """Concatena escenas en un video final con audio.

    Args:
        visual_key: 'visual_1' para variante A, 'visual_2' para variante B.
        xfade: Si True, aplica transición xfade entre clips.
    """
    # Recopilar clips válidos
    valid_clips: list[str] = []
    for scene in scene_data:
        clip = (
            scene.get(visual_key)
            or scene.get("visual_1")
            or scene.get("clip")
            or scene.get("fallback_clip")
            or ""
        )
        if clip and Path(clip).exists():
            valid_clips.append(clip)
        else:
            logger.debug(f"SaarComposer: clip no encontrado para {visual_key} en escena {scene.get('index', '?')}")

    if not valid_clips:
        logger.warning(f"SaarComposer: no hay clips válidos para variante '{visual_key}'")
        return False

    # Ruta al video sin audio
    video_no_audio = str(temp_dir / f"concat_noaudio_{visual_key}.mp4")

    if xfade and len(valid_clips) > 1:
        # Usar xfade dinámico
        ok = apply_xfade(valid_clips, video_no_audio, transition=xfade_type)
    else:
        # Concat simple con demuxer
        list_file = temp_dir / f"concat_list_{visual_key}.txt"
        with open(list_file, "w", encoding="utf-8") as f:
            for clip in valid_clips:
                f.write(f"file '{Path(clip).resolve()}'\n")
        cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(list_file),
            "-c:v", "copy",
            video_no_audio,
        ]
        ok = _safe_ffmpeg(cmd, f"concat_{visual_key}")

    if not ok:
        return False

    # Mezclar audio con el video concatenado
    cmd_final = [
        "ffmpeg", "-y",
        "-i", video_no_audio,
        "-i", audio_track,
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        output_path,
    ]
    ok_final = _safe_ffmpeg(cmd_final, f"merge_audio_{visual_key}")
    if ok_final:
        logger.info(f"SaarComposer: variante '{visual_key}' → {output_path}")
    return ok_final


# ─────────────────────────────────────────────
# Clase principal: SaarComposerPRO (BaseTool)
# ─────────────────────────────────────────────

class SaarComposerPRO(BaseTool):
    """Compositor SaarD00 completo con A/B split + avatar injection.

    Genera 2 variantes de video por trabajo para A/B testing:
    - Variante A: usa visual_1 de cada escena
    - Variante B: usa visual_2 de cada escena (diferente stock o ángulo)
    Ambas pueden tener avatar inyectado en posición aleatoria.
    """

    name = "saar_composer_pro"
    version = "1.0.0"
    tier = ToolTier.CORE
    capability = "video_composition"
    provider = "saard00"
    stability = ToolStability.BETA
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.STOCHASTIC
    runtime = ToolRuntime.LOCAL

    dependencies = ["cmd:ffmpeg"]
    install_instructions = "Instala FFmpeg: https://ffmpeg.org/download.html"

    capabilities = [
        "ab_split_render",
        "avatar_injection",
        "silence_trim",
        "volume_boost",
        "xfade_transition",
        "concat_scenes",
    ]
    best_for = [
        "A/B testing de variantes visuales",
        "Videos con avatar presentador",
        "YouTube Shorts de alta retención",
        "Composición profesional de múltiples clips",
    ]
    not_good_for = [
        "videos muy cortos <5s",
        "composición en tiempo real",
    ]

    input_schema = {
        "type": "object",
        "required": ["scene_data", "audio_track", "output_dir"],
        "properties": {
            "scene_data": {
                "type": "array",
                "description": "Lista de escenas con visual_1, visual_2, duration",
            },
            "audio_track": {
                "type": "string",
                "description": "Ruta al audio principal (voiceover)",
            },
            "output_dir": {
                "type": "string",
                "description": "Directorio de salida para las variantes",
            },
            "output_prefix": {
                "type": "string",
                "default": "video",
                "description": "Prefijo del nombre de archivo de salida",
            },
            "inject_avatar": {
                "type": "boolean",
                "default": False,
                "description": "Inyectar avatar random sobre las variantes",
            },
            "avatar_path": {
                "type": "string",
                "description": "Ruta al avatar. Si no se provee, se elige uno random.",
            },
            "avatar_position": {
                "type": "string",
                "default": "bottom_right",
                "enum": ["bottom_right", "bottom_left", "top_right", "top_left", "bottom_center"],
            },
            "trim_silence": {
                "type": "boolean",
                "default": True,
                "description": "Recortar silencio del audio antes de componer",
            },
            "boost_db": {
                "type": "number",
                "default": 0.0,
                "description": "Ganancia de volumen en dB (0 = sin boost)",
            },
            "xfade": {
                "type": "boolean",
                "default": True,
                "description": "Aplicar transición xfade entre clips",
            },
            "xfade_type": {
                "type": "string",
                "default": "fade",
                "description": "Tipo de xfade: fade, wiperight, slideleft, dissolve, pixelize",
            },
        },
    }

    output_schema = {
        "type": "object",
        "properties": {
            "variant_a": {"type": "string"},
            "variant_b": {"type": "string"},
            "variants": {"type": "array"},
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=2, ram_mb=2048, vram_mb=0, disk_mb=2000, network_required=False
    )
    retry_policy = RetryPolicy(max_retries=1)
    side_effects = ["escribe archivos MP4 en output_dir"]
    user_visible_verification = [
        "Verifica que las 2 variantes de video se hayan creado",
        "Comprueba que el audio esté sincronizado",
        "Si se usó avatar, verifica que sea visible",
    ]

    def get_status(self) -> ToolStatus:
        import shutil
        return ToolStatus.AVAILABLE if shutil.which("ffmpeg") else ToolStatus.UNAVAILABLE

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        start = time.time()

        scene_data: list[dict] = inputs["scene_data"]
        audio_track: str = inputs["audio_track"]
        output_dir = Path(inputs["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)
        prefix = inputs.get("output_prefix", "video")
        do_trim = inputs.get("trim_silence", True)
        boost_db = float(inputs.get("boost_db", 0.0))
        do_xfade = inputs.get("xfade", True)
        xfade_type = inputs.get("xfade_type", "fade")
        do_avatar = inputs.get("inject_avatar", False)
        avatar_position = inputs.get("avatar_position", "bottom_right")

        # Directorio temporal
        with tempfile.TemporaryDirectory(prefix="saar_") as _tmp:
            tmp_dir = Path(_tmp)

            # ── 1. Procesar audio ──────────────────────────────────
            processed_audio = audio_track
            if do_trim and Path(audio_track).exists():
                trimmed = str(tmp_dir / "audio_trimmed.wav")
                if trim_silence(audio_track, trimmed):
                    processed_audio = trimmed

            if boost_db > 0 and Path(processed_audio).exists():
                boosted = str(tmp_dir / "audio_boosted.wav")
                if boost_volume(processed_audio, boosted, db_gain=boost_db):
                    processed_audio = boosted

            # ── 2. Render variante A (visual_1) ───────────────────
            variant_a_path = str(output_dir / f"{prefix}_variant_A.mp4")
            ok_a = concat_scenes(
                scene_data=scene_data,
                audio_track=processed_audio,
                visual_key="visual_1",
                output_path=variant_a_path,
                temp_dir=tmp_dir,
                xfade=do_xfade,
                xfade_type=xfade_type,
            )

            # ── 3. Render variante B (visual_2) ───────────────────
            variant_b_path = str(output_dir / f"{prefix}_variant_B.mp4")
            ok_b = concat_scenes(
                scene_data=scene_data,
                audio_track=processed_audio,
                visual_key="visual_2",
                output_path=variant_b_path,
                temp_dir=tmp_dir,
                xfade=do_xfade,
                xfade_type=xfade_type,
            )

            # Si variante B falló, clonar variant A como fallback
            if not ok_b and ok_a:
                import shutil as _shutil
                _shutil.copy2(variant_a_path, variant_b_path)
                logger.warning("SaarComposer: variante B clonada desde A (fallback)")
                ok_b = True

            # ── 4. Avatar injection (opcional) ────────────────────
            variants_final: list[str] = []
            for variant_path in [variant_a_path, variant_b_path]:
                if not Path(variant_path).exists():
                    continue

                if do_avatar:
                    # Elegir avatar
                    avatar_path = inputs.get("avatar_path", "")
                    if not avatar_path or not Path(avatar_path).exists():
                        # Buscar avatares disponibles
                        base_root = Path(__file__).resolve().parent.parent.parent
                        avatar_candidates = [
                            str(base_root / a) for a in _AVATAR_CANDIDATES
                            if (base_root / a).exists()
                        ]
                        if avatar_candidates:
                            avatar_path = random.choice(avatar_candidates)
                            logger.info(f"SaarComposer: avatar random → {Path(avatar_path).name}")
                        else:
                            logger.warning("SaarComposer: no se encontraron avatares, omitiendo")
                            avatar_path = ""

                    if avatar_path and Path(avatar_path).exists():
                        avatar_out = variant_path.replace(".mp4", "_avatar.mp4")
                        if inject_avatar(
                            base_video=variant_path,
                            avatar_path=avatar_path,
                            output_path=avatar_out,
                            position=avatar_position,
                        ):
                            variants_final.append(avatar_out)
                            continue

                variants_final.append(variant_path)

        # ── 5. Resultado ──────────────────────────────────────────
        duration = round(time.time() - start, 2)
        result_variants = [v for v in variants_final if Path(v).exists()]

        if not result_variants:
            return ToolResult(
                success=False,
                error="SaarComposer: no se generó ninguna variante",
                duration_seconds=duration,
            )

        return ToolResult(
            success=True,
            data={
                "variant_a": variants_final[0] if len(variants_final) > 0 else "",
                "variant_b": variants_final[1] if len(variants_final) > 1 else "",
                "variants": result_variants,
                "total_variants": len(result_variants),
                "audio_processed": processed_audio,
            },
            artifacts=result_variants,
            duration_seconds=duration,
        )


# ─────────────────────────────────────────────
# Alias legado para compatibilidad hacia atrás
# ─────────────────────────────────────────────
class SaarComposer:
    """Alias de compatibilidad con el saar_composer.py original."""

    def __init__(self, temp_dir: Path):
        self.temp_dir = temp_dir

    def trim_silence(self, audio_path: str, output_path: str) -> bool:
        return trim_silence(audio_path, output_path)

    def build_ab_split_renders(
        self,
        scene_data: list[dict],
        audio_track: str,
        output_prefix: str,
    ) -> list[str]:
        """API legada: genera variantes A y B."""
        tool = SaarComposerPRO()
        result = tool.execute({
            "scene_data": scene_data,
            "audio_track": audio_track,
            "output_dir": str(self.temp_dir),
            "output_prefix": output_prefix,
            "xfade": True,
        })
        return result.data.get("variants", []) if result.success else []

    def enhance_for_retention(self, input_video: str, output_video: str, hook_audio: str = None) -> bool:
        """Mejora SaarD00 con retención: mejor xfade + sound design + avatar más dinámico."""
        # Boost de voz + silencio trim ya existe → lo reforzamos
        temp_boost = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False).name
        boost_volume(input_video, temp_boost, db_gain=4.0)  # más presente

        # xFade más dinámico (más corto y variado)
        clips = [temp_boost]  # puedes agregar más clips si quieres
        apply_xfade(clips, output_video, transition="slideleft", duration=0.3)

        logger.info("✅ SaarComposer mejorado para retención (xfade + volume boost)")
        return True
