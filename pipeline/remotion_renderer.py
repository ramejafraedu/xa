"""Remotion Renderer Wrapper - V16 PRO

Integra Remotion como provider principal de renderizado.
Forzar Remotion sin fallback a FFmpeg nativo.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Optional, Union

from loguru import logger

from config import settings


REMOTION_DIR = Path(__file__).parent.parent / "remotion-composer"


def render_with_remotion(
    clips: list[Union[str, Path]],
    audio_path: Path,
    output_path: Path,
    title: str,
    hook: str,
    nicho_slug: str,
    timestamp: int,
    subtitles_path: Optional[Path] = None,
    images: Optional[list[Path]] = None,
    composition: str = "CinematicRenderer",
    timeout_seconds: int = 300,
) -> tuple[bool, str]:
    """Render video using Remotion as primary provider.
    
    Args:
        clips: List of video clip URLs or paths
        audio_path: Path to audio file
        output_path: Path for output video
        title: Video title
        hook: Hook text
        nicho_slug: Niche slug
        timestamp: Timestamp for unique identification
        subtitles_path: Optional path to subtitles VTT file
        images: Optional list of image paths for intro/outro
        composition: Remotion composition to use
        timeout_seconds: Render timeout
    
    Returns:
        (success, message_or_path)
    """
    try:
        # Verificar que Remotion esté instalado
        if not (REMOTION_DIR / "node_modules").exists():
            logger.error("❌ Remotion not installed. Run: cd remotion-composer && npm install")
            return False, "Remotion not installed"
        
        # Preparar input.json para Remotion
        input_data = {
            "title": title,
            "hook": hook,
            "nichoSlug": nicho_slug,
            "timestamp": timestamp,
            "audioPath": str(audio_path.resolve()),
            "outputPath": str(output_path.resolve()),
            "clips": [str(c) if isinstance(c, Path) else c for c in clips],
            "images": [str(img.resolve()) for img in (images or [])] if images else [],
            "subtitlesPath": str(subtitles_path.resolve()) if subtitles_path else None,
        }
        
        input_json_path = REMOTION_DIR / f"input_{timestamp}.json"
        with open(input_json_path, 'w', encoding='utf-8') as f:
            json.dump(input_data, f, ensure_ascii=False, indent=2)
        
        # Comando de render Remotion
        cmd = [
            "npx", "remotion", "render",
            "src/index.tsx",
            composition,
            str(output_path.resolve()),
            "--props", str(input_json_path),
            "--log=verbose",
            "--concurrency", str(settings.remotion_concurrency or 4),
        ]
        
        logger.info(f"🎬 Starting Remotion render: {composition}")
        logger.info(f"   Output: {output_path.name}")
        
        # Ejecutar render
        result = subprocess.run(
            cmd,
            cwd=REMOTION_DIR,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            encoding='utf-8',
            errors='ignore'
        )
        
        # Limpiar input.json
        input_json_path.unlink(missing_ok=True)
        
        if result.returncode != 0:
            logger.error(f"❌ Remotion render failed:\n{result.stderr}")
            return False, f"Remotion error: {result.stderr[:500]}"
        
        # Verificar output
        if output_path.exists() and output_path.stat().st_size > 1000:
            logger.info(f"✅ Remotion render complete: {output_path.name}")
            return True, str(output_path)
        else:
            return False, "Remotion output file not created or empty"
            
    except subprocess.TimeoutExpired:
        logger.error(f"⏱️ Remotion render timed out after {timeout_seconds}s")
        return False, "Render timeout"
    except Exception as e:
        logger.error(f"❌ Remotion render exception: {e}")
        return False, str(e)


def is_remotion_available() -> bool:
    """Check if Remotion is properly installed and available."""
    try:
        if not REMOTION_DIR.exists():
            return False
        if not (REMOTION_DIR / "node_modules").exists():
            return False
        
        # Test npx remotion --version
        result = subprocess.run(
            ["npx", "remotion", "--version"],
            cwd=REMOTION_DIR,
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


def ensure_remotion_deps() -> tuple[bool, str]:
    """Ensure Remotion dependencies are installed."""
    if not REMOTION_DIR.exists():
        return False, f"Remotion directory not found: {REMOTION_DIR}"
    
    if (REMOTION_DIR / "node_modules").exists():
        return True, "Remotion already installed"
    
    logger.info("📦 Installing Remotion dependencies...")
    try:
        result = subprocess.run(
            ["npm", "install"],
            cwd=REMOTION_DIR,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode == 0:
            return True, "Remotion installed successfully"
        else:
            return False, f"npm install failed: {result.stderr}"
    except Exception as e:
        return False, str(e)
