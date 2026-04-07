"""Video Factory V15/V16 — Reference Agent.

Analyzes a public reference video URL to extract pacing signals:
  - average cut duration
  - estimated hook duration
  - delivery promise hint (motion-led vs hybrid)

If video download is unavailable, it falls back to text reference context.
"""
from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

from loguru import logger

from core.reference_context import load_reference_context


class ReferenceAgent:
    """Extract structural cues from a reference URL."""

    def run(self, reference_url: str, cache_dir: Path) -> dict:
        """Analyze a reference URL and return normalized pacing metadata."""
        url = (reference_url or "").strip()
        if not url:
            return {}

        analysis = {
            "reference_url": url,
            "video_available": False,
            "video_path": "",
            "video_duration_seconds": 0.0,
            "avg_cut_seconds": 2.2,
            "hook_seconds": 1.8,
            "delivery_promise": "hybrid",
            "key_moments": [],
            "confidence": 0.30,
            "analysis_method": "text_fallback",
        }

        try:
            video_path = self._download_reference_video(url, cache_dir)
            if video_path and video_path.exists():
                duration = self._probe_duration(video_path)
                scene_times = self._detect_scene_changes(video_path, duration)

                cut_count = max(0, len(scene_times) - 1)
                if duration > 0 and cut_count > 0:
                    avg_cut = duration / cut_count
                else:
                    avg_cut = 2.2

                motion_density = cut_count / max(duration, 1.0)
                promise = "motion_led" if motion_density >= 0.45 else "hybrid"

                analysis.update(
                    {
                        "video_available": True,
                        "video_path": str(video_path.resolve().as_posix()),
                        "video_duration_seconds": round(duration, 3),
                        "avg_cut_seconds": round(max(0.8, min(6.0, avg_cut)), 3),
                        "hook_seconds": round(max(0.8, min(3.0, avg_cut * 1.2)), 3),
                        "delivery_promise": promise,
                        "key_moments": self._extract_key_moments(scene_times, duration),
                        "confidence": 0.75 if cut_count >= 4 else 0.55,
                        "analysis_method": "video_probe",
                    }
                )
                return analysis
        except Exception as exc:
            logger.debug(f"Reference video analysis failed, fallback to text: {exc}")

        # Text fallback
        try:
            cache_path = cache_dir / "reference_context_cache.json"
            ctx = load_reference_context(url, cache_path)
            if ctx:
                points = list(ctx.get("key_points", []))[:4]
                summary = str(ctx.get("summary", ""))
                if not points and summary:
                    points = [summary[:180]]

                analysis.update(
                    {
                        "key_moments": points,
                        "delivery_promise": self._infer_promise_from_text(summary, points),
                        "confidence": 0.35,
                        "analysis_method": "text_context",
                    }
                )
        except Exception as exc:
            logger.debug(f"Reference text fallback failed: {exc}")

        return analysis

    def _download_reference_video(self, url: str, cache_dir: Path) -> Optional[Path]:
        """Download reference video via yt-dlp when available."""
        media_dir = cache_dir / "reference_media"
        media_dir.mkdir(parents=True, exist_ok=True)

        slug = hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]
        existing = sorted(media_dir.glob(f"ref_{slug}.*"))
        for candidate in existing:
            if candidate.is_file() and candidate.stat().st_size > 1500:
                return candidate

        output_tpl = media_dir / f"ref_{slug}.%(ext)s"
        base_args = [
            "yt-dlp",
            "--no-playlist",
            "-f",
            "mp4/best[height<=1080]/best",
            "-o",
            str(output_tpl),
            url,
        ]

        commands = [
            base_args,
            [
                sys.executable,
                "-m",
                "yt_dlp",
                "--no-playlist",
                "-f",
                "mp4/best[height<=1080]/best",
                "-o",
                str(output_tpl),
                url,
            ],
        ]

        launcher_found = False
        last_error = ""
        for cmd in commands:
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=180,
                )
                launcher_found = True
                if result.returncode != 0:
                    last_error = (result.stderr or result.stdout or "")[-240:]
                    logger.debug(f"yt-dlp failed ({cmd[0]}): {last_error}")
                    continue

                downloaded = sorted(media_dir.glob(f"ref_{slug}.*"))
                for candidate in downloaded:
                    if candidate.is_file() and candidate.stat().st_size > 1500:
                        return candidate
            except FileNotFoundError:
                logger.debug(f"yt-dlp launcher unavailable: {cmd[0]}")
                continue
            except Exception as exc:
                last_error = str(exc)
                logger.debug(f"Reference download attempt failed ({cmd[0]}): {exc}")

        if not launcher_found:
            logger.debug("yt-dlp not installed; reference video download skipped")
        elif last_error:
            logger.debug(f"Reference download failed after retries: {last_error}")

        return None

    def _probe_duration(self, video_path: Path) -> float:
        """Get video duration using ffprobe."""
        try:
            result = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "quiet",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    str(video_path),
                ],
                capture_output=True,
                text=True,
                timeout=25,
            )
            if result.returncode == 0:
                return float((result.stdout or "0").strip() or 0)
        except Exception:
            pass
        return 0.0

    def _detect_scene_changes(self, video_path: Path, duration: float) -> list[float]:
        """Detect scene-change timestamps with FFmpeg scene filter."""
        timestamps = [0.0]
        try:
            result = subprocess.run(
                [
                    "ffmpeg",
                    "-i",
                    str(video_path),
                    "-vf",
                    "select='gt(scene,0.30)',showinfo",
                    "-f",
                    "null",
                    "-",
                ],
                capture_output=True,
                text=True,
                timeout=120,
            )
            stderr = result.stderr or ""
            matches = re.findall(r"pts_time:([\d.]+)", stderr)
            for m in matches:
                t = float(m)
                if t > 0:
                    timestamps.append(t)
        except Exception as exc:
            logger.debug(f"Scene detection fallback failed: {exc}")

        timestamps = sorted(set(round(t, 3) for t in timestamps))
        if duration > 0:
            if not timestamps or timestamps[-1] < duration:
                timestamps.append(round(duration, 3))
        elif len(timestamps) == 1:
            timestamps.append(10.0)

        return timestamps

    def _extract_key_moments(self, timestamps: list[float], duration: float) -> list[str]:
        """Build human-readable cues from scene boundaries."""
        if len(timestamps) < 2:
            return []

        moments: list[str] = []
        if len(timestamps) > 1:
            moments.append(f"Hook beat around {timestamps[1]:.1f}s")
        if duration > 0:
            moments.append(f"Mid reveal around {max(0.1, duration * 0.5):.1f}s")
            moments.append(f"Payoff near {max(0.1, duration * 0.82):.1f}s")
        return moments[:4]

    @staticmethod
    def _infer_promise_from_text(summary: str, points: list[str]) -> str:
        """Infer delivery promise when only text context is available."""
        text = f"{summary} {' '.join(points)}".lower()
        motion_hints = ("cinematic", "rápido", "viral", "edición", "impacto", "shorts", "tiktok")
        if any(k in text for k in motion_hints):
            return "motion_led"
        return "hybrid"
