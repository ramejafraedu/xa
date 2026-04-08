"""FFmpeg Renderer — full video assembly pipeline.

Replaces n8n nodes: ⬇️ Descargar + 🎥 FFmpeg Final.
All paths use pathlib + .as_posix() for Windows FFmpeg compatibility.
Includes self-healing error capture for the unified healer.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Optional

from loguru import logger

from config import settings
from services.http_client import download_file


# Global color grading filter (identical to MASTER V13)
GLOBAL_GRADE_VF = (
    "eq=saturation=0.90:contrast=1.06:brightness=0.012:gamma=0.97,"
    "colorbalance=rs=0.030:gs=0.012:bs=-0.020,"
    "curves=r='0/0 0.30/0.26 0.75/0.84 1/1':g='0/0 0.30/0.27 0.75/0.82 1/1':b='0/0 0.30/0.23 0.75/0.78 1/1',"
    "noise=alls=4:allf=t,"
    "format=yuv420p"
)


def download_clips(
    video_urls: list[str],
    timestamp: int,
    temp_dir: Path,
) -> list[Path]:
    """Download video clips in parallel-ish fashion.

    Returns list of successfully downloaded clip paths.
    """
    results = []
    for i, item in enumerate(video_urls, 1):
        if not item or item in ("undefined", "null", "None", ""):
            continue
        
        if isinstance(item, dict):
            url = item.get("url", "")
            local_path_str = item.get("local_path")
            if not local_path_str:
                continue
            
            dest = Path(local_path_str)
            
            # Already in cache
            if not url and dest.exists():
                results.append(dest)
                logger.debug(f"Clip {i} CACHED: {dest.name}")
                continue
                
            # Needs download
            if url and download_file(url, dest, timeout=90):
                results.append(dest)
                logger.debug(f"Clip {i} DOWNLOADED: {dest.name}")
            else:
                logger.warning(f"Clip {i} failed to download: {url[:60]}")
                
        elif isinstance(item, str):
            # Legacy string URL
            dest = temp_dir / f"clip{i}_{timestamp}.mp4"
            if download_file(item, dest, timeout=90):
                results.append(dest)
                logger.debug(f"Clip {i} OK ({dest.stat().st_size // 1024} KB)")
            else:
                logger.warning(f"Clip {i} fallback failed")

    logger.info(f"Loaded/Downloaded {len(results)}/{len(video_urls)} clips")
    return results


def download_music(url: str, timestamp: int, temp_dir: Path) -> Optional[Path]:
    """Download background music."""
    if not url or url in ("undefined", "null"):
        return None
    dest = temp_dir / f"musica_{timestamp}.mp3"
    if download_file(url, dest, timeout=30):
        return dest
    return None


def render_video(
    clips: list[Path],
    audio_path: Path,
    subs_path: Optional[Path],
    music_path: Optional[Path],
    images: list[Path],
    timestamp: int,
    temp_dir: Path,
    output_dir: Path,
    nicho_slug: str,
    gancho: str,
    titulo: str,
    duracion_audio: float,
    velocidad: str = "rapido",
    num_clips: int = 8,
    duraciones_clips: Optional[list[float]] = None,
    render_fixes: Optional[dict] = None,
) -> tuple[Optional[Path], Optional[Path], str]:
    """Render the final video. Returns (video_path, thumb_path, error_or_empty).

    If render_fixes is provided, applies corrected parameters from the self-healer.
    """
    if not clips and not images:
        return None, None, "No clips and no images to render"

    # --- Configuration ---
    preset = "veryfast"
    crf = "22"
    extra_flags: list[str] = []
    remove_filters: list[str] = []

    if render_fixes:
        preset = render_fixes.get("change_preset", preset)
        extra_flags = render_fixes.get("add_flags", [])
        remove_filters = render_fixes.get("remove_filters", [])

    # --- Step 1: Create intro from image ---
    intro_path = None
    if images:
        intro_path = _create_intro(images[0], timestamp, temp_dir)

    # --- Step 2: Process clips (crop, scale, effects) ---
    processed, lista_lines = _process_clips(
        clips=clips,
        timestamp=timestamp,
        temp_dir=temp_dir,
        duracion_audio=duracion_audio,
        velocidad=velocidad,
        num_clips=num_clips,
        duraciones_clips=duraciones_clips,
        has_intro=intro_path is not None,
        images=images,
        preset=preset,
        crf=crf,
        remove_filters=remove_filters,
    )

    using_image_fallback = False
    if processed == 0:
        fallback_lines = _build_image_fallback_timeline(
            images=images,
            timestamp=timestamp,
            temp_dir=temp_dir,
            target_duration=duracion_audio,
        )
        if fallback_lines:
            logger.warning(
                "No clips processed successfully; switching to image-only motion timeline "
                "to avoid intro-only output"
            )
            intro_path = None
            lista_lines = fallback_lines
            using_image_fallback = True
        elif intro_path:
            return None, None, "No clips processed successfully (intro-only output blocked)"

    if not processed and not intro_path:
        return None, None, "No clips processed successfully"

    # Add intro at start of concat list
    if intro_path:
        lista_lines.insert(0, f"file '{intro_path.as_posix()}'")

    # --- Step 3: Insert mid-video images ---
    if not using_image_fallback:
        lista_lines = _insert_mid_images(
            lista_lines, images[1:], timestamp, temp_dir, num_clips
        )

    if not lista_lines:
        return None, None, "No visual segments generated for concat"

    # --- Step 4: Concat all segments ---
    lista_file = temp_dir / f"lista_{timestamp}.txt"
    lista_file.write_text("\n".join(lista_lines) + "\n", encoding="utf-8")

    video_no_audio = temp_dir / f"video_sin_audio_{timestamp}.mp4"
    concat_cmd = [
        "ffmpeg", "-y", "-threads", "2",
        "-f", "concat", "-safe", "0",
        "-i", lista_file.as_posix(),
        "-vf", GLOBAL_GRADE_VF,
        "-c:v", "libx264", "-preset", preset, "-crf", crf,
        "-pix_fmt", "yuv420p", "-an",
        "-movflags", "+faststart",
        *_privacy_ffmpeg_args(),
        *extra_flags,
        video_no_audio.as_posix(),
    ]

    result = _run_ffmpeg(concat_cmd, "concat")
    if result:
        return None, None, f"Concat failed: {result}"

    # --- Step 5: Mix audio + music ---
    video_final = temp_dir / f"video_final_{timestamp}.mp4"
    audio_error = _mix_audio(
        video_no_audio, audio_path, music_path,
        duracion_audio, video_final, preset, extra_flags,
    )
    if audio_error:
        return None, None, f"Audio mix failed: {audio_error}"

    # --- Step 6: Burn subtitles ---
    if subs_path and subs_path.exists() and subs_path.stat().st_size > 100:
        sub_error = _burn_subtitles(video_final, subs_path, temp_dir, timestamp, preset, extra_flags)
        if sub_error:
            logger.warning(f"Subtitle burn failed (non-fatal): {sub_error}")

    # --- Step 7: Generate thumbnail ---
    thumb_path = _generate_thumbnail(
        video_final, images, gancho, titulo, timestamp, temp_dir,
    )

    # --- Step 8: Move to output ---
    gancho_slug = _slugify(gancho, 38)
    nicho_s = _slugify(nicho_slug, 24)
    final_name = f"{nicho_s}_{gancho_slug}_{timestamp}.mp4"
    final_path = output_dir / final_name
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        import shutil
        shutil.move(str(video_final), str(final_path))
    except Exception as e:
        return None, None, f"Failed to move video to output: {e}"

    # Extra safety pass: strip container metadata from final published file.
    md_error = _sanitize_output_metadata(final_path, temp_dir, timestamp)
    if md_error:
        logger.warning(f"Final metadata cleanup failed (non-fatal): {md_error}")

    logger.info(f"✅ Video rendered: {final_path.name} ({final_path.stat().st_size // 1024 // 1024}MB)")
    return final_path, thumb_path, ""


def _create_intro(image: Path, timestamp: int, temp_dir: Path) -> Optional[Path]:
    """Create a 2s intro video from an image."""
    intro = temp_dir / f"intro_{timestamp}.mp4"
    vf = (
        "scale=1080:1920:force_original_aspect_ratio=decrease,"
        "pad=1080:1920:(ow-iw)/2:(oh-ih)/2,setsar=1,"
        "fade=t=out:st=1.5:d=0.5"
    )
    cmd = [
        "ffmpeg", "-y", "-loop", "1",
        "-i", image.as_posix(),
        "-vf", vf,
        "-t", "2", "-r", "30",
        "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "fast",
        *_privacy_ffmpeg_args(),
        intro.as_posix(),
    ]
    error = _run_ffmpeg(cmd, "intro")
    if error or not intro.exists() or intro.stat().st_size < 1000:
        return None
    return intro


def _process_clips(
    clips: list[Path],
    timestamp: int,
    temp_dir: Path,
    duracion_audio: float,
    velocidad: str,
    num_clips: int,
    duraciones_clips: Optional[list[float]],
    has_intro: bool,
    images: list[Path],
    preset: str,
    crf: str,
    remove_filters: list[str],
) -> tuple[int, list[str]]:
    """Process raw clips: crop, scale, effects. Returns (ok_count, lista_lines)."""
    # Calculate durations
    mid_reserve = sum(
        [1.4, 1.5, 1.3][:len(images) - 1]
    ) if len(images) > 1 else 0

    target = max(4.0, duracion_audio - mid_reserve - (2.0 if has_intro else 0.0))

    if duraciones_clips and len(duraciones_clips) >= len(clips):
        durations = [float(d) for d in duraciones_clips[:len(clips)]]
    else:
        durations = [target / max(len(clips), 1)] * len(clips)

    total = sum(durations) or 1
    durations = [d * target / total for d in durations]

    lista_lines = []
    ok = 0

    for i, clip in enumerate(clips):
        out = temp_dir / f"recortado{i+1}_{timestamp}.mp4"
        dur = round(durations[i] if i < len(durations) else 2.0, 3)
        fade_dur = 0.15
        fade_out_st = max(0, dur - fade_dur)

        vf = (
            f"crop='if(gt(a,9/16),ih*9/16,iw)':'if(gt(a,9/16),ih,iw*16/9)':'(iw-ow)/2':'(ih-oh)/2',"
            f"scale=1080x1920:flags=lanczos,fps=30,"
            f"eq=contrast=1.12:brightness=0.03:saturation=1.15:gamma=1.05,"
            f"unsharp=lx=5:ly=5:la=1.2,"
            f"fade=t=in:st=0:d={fade_dur},fade=t=out:st={fade_out_st}:d={fade_dur}"
        )

        # Add zoompan based on speed (unless healer removed it)
        if "zoompan" not in remove_filters:
            if velocidad == "ultra_rapido":
                vf += ",zoompan=z='if(lte(on,4),1.08,zoom-0.0015)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=2:s=1080x1920:fps=30"
            elif velocidad == "rapido":
                vf += ",zoompan=z='if(lte(on,6),1.05,zoom-0.001)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=3:s=1080x1920:fps=30"

        cmd = [
            "ffmpeg", "-y", "-threads", "2",
            "-i", clip.as_posix(),
            "-t", str(dur),
            "-vf", vf,
            "-an", "-c:v", "libx264",
            "-preset", preset, "-crf", crf,
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            "-max_muxing_queue_size", "1024",
            *_privacy_ffmpeg_args(),
            out.as_posix(),
        ]

        error = _run_ffmpeg(cmd, f"clip{i+1}")
        if not error and out.exists() and out.stat().st_size > 1000:
            lista_lines.append(f"file '{out.as_posix()}'")
            ok += 1
        else:
            logger.warning(f"Clip {i+1} processing failed")

    return ok, lista_lines


def _insert_mid_images(
    lista_lines: list[str],
    images: list[Path],
    timestamp: int,
    temp_dir: Path,
    num_clips: int,
) -> list[str]:
    """Insert image segments at strategic positions across the timeline."""
    if not images:
        return lista_lines

    position_fractions = [0.18, 0.32, 0.46, 0.60, 0.74, 0.86, 0.94]
    durations = [1.2, 1.3, 1.4, 1.4, 1.3, 1.2, 1.1]
    use_count = min(len(images), len(position_fractions))

    specs = []
    for idx in range(use_count):
        dur = durations[idx]
        pos = max(1, round(max(1, num_clips) * position_fractions[idx]))
        specs.append((dur, pos))

    created = []
    for idx, (img, (dur, pos)) in enumerate(zip(images[:use_count], specs)):
        if not img.exists() or img.stat().st_size < 1000:
            continue

        seg = temp_dir / f"imgseg_{idx+2}_{timestamp}.mp4"
        vf = (
            "scale=1080:1920:force_original_aspect_ratio=decrease,"
            "pad=1080:1920:(ow-iw)/2:(oh-ih)/2,"
            "zoompan=z='if(lte(on,45),1.06,zoom-0.0008)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s=1080x1920:fps=30"
        )
        cmd = [
            "ffmpeg", "-y", "-loop", "1",
            "-i", img.as_posix(),
            "-t", f"{dur:.3f}",
            "-vf", vf,
            "-an", "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "23", "-pix_fmt", "yuv420p",
            *_privacy_ffmpeg_args(),
            seg.as_posix(),
        ]
        error = _run_ffmpeg(cmd, f"imgseg{idx+2}")
        if not error and seg.exists() and seg.stat().st_size > 1000:
            created.append((pos, seg))

    if not created:
        return lista_lines

    # Insert at positions
    out = []
    clips_seen = 0
    cursor = 0
    for line in lista_lines:
        out.append(line)
        if "recortado" in line:
            clips_seen += 1
            while cursor < len(created) and created[cursor][0] <= clips_seen:
                out.append(f"file '{created[cursor][1].as_posix()}'")
                cursor += 1

    while cursor < len(created):
        out.append(f"file '{created[cursor][1].as_posix()}'")
        cursor += 1

    return out


def _build_image_fallback_timeline(
    images: list[Path],
    timestamp: int,
    temp_dir: Path,
    target_duration: float,
) -> list[str]:
    """Build full-duration visual timeline from images when video clips fail."""
    valid_images = [img for img in images if img.exists() and img.stat().st_size > 1000]
    if not valid_images:
        return []

    segment_duration = 2.8
    target_total = max(4.0, float(target_duration or 0.0))
    remaining = target_total
    segments: list[str] = []
    generated = 0.0
    idx = 0

    while remaining > 0.05 and idx < 120:
        dur = min(segment_duration, remaining)
        img = valid_images[idx % len(valid_images)]
        seg = temp_dir / f"imgfill_{idx+1}_{timestamp}.mp4"

        if idx % 2 == 0:
            zoompan = (
                "zoompan=z='if(lte(on,36),1.07,zoom-0.0008)':"
                "x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s=1080x1920:fps=30"
            )
        else:
            zoompan = (
                "zoompan=z='if(lte(on,36),1.04,zoom+0.0006)':"
                "x='(iw-iw/zoom)/3':y='(ih-ih/zoom)/3':d=1:s=1080x1920:fps=30"
            )

        fade_dur = 0.18 if dur > 0.6 else 0.10
        fade_out_st = max(0.0, dur - fade_dur)
        vf = (
            "scale=1080:1920:force_original_aspect_ratio=decrease,"
            "pad=1080:1920:(ow-iw)/2:(oh-ih)/2,"
            "setsar=1,"
            f"{zoompan},"
            f"fade=t=in:st=0:d={fade_dur},fade=t=out:st={fade_out_st}:d={fade_dur}"
        )

        cmd = [
            "ffmpeg", "-y", "-loop", "1",
            "-i", img.as_posix(),
            "-t", f"{dur:.3f}",
            "-vf", vf,
            "-an", "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "23", "-pix_fmt", "yuv420p",
            *_privacy_ffmpeg_args(),
            seg.as_posix(),
        ]

        error = _run_ffmpeg(cmd, f"imgfill{idx+1}")
        if not error and seg.exists() and seg.stat().st_size > 1000:
            segments.append(f"file '{seg.as_posix()}'")
            remaining -= dur
            generated += dur
        else:
            logger.warning(f"Image fallback segment failed: {img.name}")

        idx += 1

    # If coverage is too low, force retry path instead of delivering a short visual timeline.
    min_required = max(3.0, target_total * 0.60)
    if generated < min_required:
        logger.warning(
            f"Image fallback coverage too low ({generated:.1f}s/{target_total:.1f}s)"
        )
        return []

    return segments


def _mix_audio(
    video: Path,
    audio: Path,
    music: Optional[Path],
    duracion: float,
    output: Path,
    preset: str,
    extra_flags: list[str],
) -> str:
    """Mix voice + music with ducking. Returns error string or empty."""
    bg_fade_out = max(0, duracion - 3)

    if music and music.exists() and music.stat().st_size > 1000:
        # Full mix with sidechain compression
        af = (
            f"[0:a]highpass=f=80,lowpass=f=15000,"
            f"alimiter=level_in=1:level_out=0.95:limit=0.98:attack=5:release=100,"
            f"asplit=2[vozduck][vozmain];"
            f"[1:a]volume=0.12,"
            f"afade=t=in:st=0:d=2,afade=t=out:st={bg_fade_out}:d=2[bg];"
            f"[bg][vozduck]sidechaincompress=threshold=-25dB:ratio=4:attack=0.01:release=0.5:makeup=1[ducked];"
            f"[ducked][vozmain]amix=inputs=2:weights='1 1':duration=first:dropout_transition=2,"
            f"loudnorm=I=-14:TP=-1.5:LRA=7[audio_final]"
        )
        cmd = [
            "ffmpeg", "-y",
            "-i", audio.as_posix(),
            "-i", music.as_posix(),
            "-filter_complex", af,
            "-map", "[audio_final]",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            *_privacy_ffmpeg_args(),
            (output.with_suffix(".m4a")).as_posix(),
        ]
        error = _run_ffmpeg(cmd, "audio_mix")
        if error:
            return error

        mixed_audio = output.with_suffix(".m4a")
        cmd2 = [
            "ffmpeg", "-y", "-threads", "2",
            "-i", video.as_posix(),
            "-i", mixed_audio.as_posix(),
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            "-t", str(duracion),
            "-movflags", "+faststart",
            *_privacy_ffmpeg_args(),
            *extra_flags,
            output.as_posix(),
        ]
        error = _run_ffmpeg(cmd2, "mux_final")
        mixed_audio.unlink(missing_ok=True)
        return error
    else:
        # Voice only
        cmd = [
            "ffmpeg", "-y", "-threads", "2",
            "-i", video.as_posix(),
            "-i", audio.as_posix(),
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            "-t", str(duracion),
            "-movflags", "+faststart",
            *_privacy_ffmpeg_args(),
            *extra_flags,
            output.as_posix(),
        ]
        return _run_ffmpeg(cmd, "mux_voice")


def _burn_subtitles(
    video: Path,
    subs: Path,
    temp_dir: Path,
    timestamp: int,
    preset: str,
    extra_flags: list[str],
) -> str:
    """Burn ASS subtitles into video. Returns error or empty."""
    output = temp_dir / f"video_sub_{timestamp}.mp4"
    # Windows absolute paths format like "C:/..." breaks ffmpeg filters due to the colon. Escape it.
    escaped_subs_path = subs.as_posix().replace(":", r"\:")
    vf = f"ass='{escaped_subs_path}'"

    cmd = [
        "ffmpeg", "-y", "-threads", "2",
        "-i", video.as_posix(),
        "-vf", vf,
        "-map", "0:v:0", "-map", "0:a?",
        "-c:v", "libx264", "-preset", preset, "-crf", "22",
        "-pix_fmt", "yuv420p", "-c:a", "copy",
        "-movflags", "+faststart",
        *_privacy_ffmpeg_args(),
        *extra_flags,
        output.as_posix(),
    ]

    error = _run_ffmpeg(cmd, "subtitles")
    if not error and output.exists() and output.stat().st_size > 1000:
        video.unlink(missing_ok=True)
        output.rename(video)
        return ""
    output.unlink(missing_ok=True)
    return error


def _generate_thumbnail(
    video: Path,
    images: list[Path],
    gancho: str,
    titulo: str,
    timestamp: int,
    temp_dir: Path,
) -> Optional[Path]:
    """Generate video thumbnail."""
    thumb = temp_dir / f"thumb_{timestamp}.jpg"

    # Use image 4 or image 1 as base
    src = None
    if len(images) >= 4 and images[3].exists():
        src = images[3]
    elif images and images[0].exists():
        src = images[0]

    if src:
        text_file = temp_dir / f"thumb_text_{timestamp}.txt"
        text_file.write_text(f"{gancho}\n{titulo}", encoding="utf-8")

        # Escape colon in Windows path for the textfile parameter
        escaped_txt_path = text_file.as_posix().replace(":", r"\:")
        vf = (
            "scale=1080:1920:force_original_aspect_ratio=decrease,"
            "pad=1080:1920:(ow-iw)/2:(oh-ih)/2,"
            f"drawtext=textfile='{escaped_txt_path}':"
            "fontcolor=white:fontsize=68:line_spacing=14:"
            "x=(w-text_w)/2:y=h*0.70:"
            "box=1:boxcolor=black@0.62:boxborderw=24"
        )
        cmd = [
            "ffmpeg", "-y",
            "-i", src.as_posix(),
            "-vf", vf,
            "-frames:v", "1",
            thumb.as_posix(),
        ]
        _run_ffmpeg(cmd, "thumb_img")
        text_file.unlink(missing_ok=True)

    if not thumb.exists() or thumb.stat().st_size < 100:
        # Fallback: extract frame from video
        cmd = [
            "ffmpeg", "-y", "-ss", "00:00:02",
            "-i", video.as_posix(),
            "-frames:v", "1",
            thumb.as_posix(),
        ]
        _run_ffmpeg(cmd, "thumb_frame")

    return thumb if thumb.exists() else None


def _run_ffmpeg(cmd: list[str], stage: str) -> str:
    """Run an FFmpeg command. Returns error string or empty on success."""
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=600,
        )
        if result.returncode != 0:
            error = result.stderr[-500:] if result.stderr else "Unknown FFmpeg error"
            logger.warning(f"FFmpeg [{stage}] failed (rc={result.returncode}): {error[-200:]}")
            return error
        return ""
    except subprocess.TimeoutExpired:
        msg = f"FFmpeg [{stage}] timed out (600s)"
        logger.error(msg)
        return msg
    except Exception as e:
        msg = f"FFmpeg [{stage}] exception: {e}"
        logger.error(msg)
        return msg


def _slugify(text: str, max_len: int = 32) -> str:
    """Convert text to a filename-safe slug."""
    import re
    slug = text.lower()
    slug = re.sub(r"[^a-z0-9]+", "_", slug)
    slug = slug.strip("_")
    return slug[:max_len] or "video"


def _privacy_ffmpeg_args() -> list[str]:
    """Common FFmpeg args to strip container metadata from outputs."""
    return [
        "-map_metadata", "-1",
        "-metadata", "title=",
        "-metadata", "comment=",
        "-metadata", "description=",
        "-metadata", "artist=",
        "-metadata", "copyright=",
        "-metadata", "encoder=",
    ]


def _sanitize_output_metadata(video_path: Path, temp_dir: Path, timestamp: int) -> str:
    """Final metadata strip pass using stream copy to keep quality untouched."""
    sanitized = temp_dir / f"video_cleanmeta_{timestamp}.mp4"
    cmd = [
        "ffmpeg", "-y", "-threads", "2",
        "-i", video_path.as_posix(),
        "-map", "0:v:0", "-map", "0:a?",
        "-c", "copy",
        "-movflags", "+faststart",
        *_privacy_ffmpeg_args(),
        sanitized.as_posix(),
    ]
    error = _run_ffmpeg(cmd, "strip_metadata")
    if error:
        sanitized.unlink(missing_ok=True)
        return error
    if not sanitized.exists() or sanitized.stat().st_size <= 1000:
        sanitized.unlink(missing_ok=True)
        return "strip_metadata produced invalid output"

    try:
        video_path.unlink(missing_ok=True)
        sanitized.rename(video_path)
        return ""
    except Exception as e:
        sanitized.unlink(missing_ok=True)
        return str(e)
