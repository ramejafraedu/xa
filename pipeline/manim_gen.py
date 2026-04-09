"""Manim Pipeline Module for Video Factory V16 PRO.

Generates professional-grade data visualizations (charts, numbers scaling)
using Manim (Math Animation Engine). Outputs transparent or chroma video 
to be overlaid in Remotion/FFmpeg.
"""

import re
import subprocess
import time
from pathlib import Path
from loguru import logger

from config import settings

# We will generate a python script with the manim code and then run it
MANIM_TEMPLATE = """
from manim import *

class FinancialChart(Scene):
    def construct(self):
        # Configurar color de fondo as Chroma if transparent not perfectly supported
        self.camera.background_color = "{bg_color}"
        
        # Título
        title = Text("{title}", font_size=36, color=WHITE).to_edge(UP)
        self.play(Write(title), run_time=1.5)
        
        # Un gráfico simple de barras o linea creciendo
        axes = Axes(
            x_range=[0, 5, 1],
            y_range=[0, 100, 20],
            axis_config={{"color": WHITE}},
        ).scale(0.8).next_to(title, DOWN, buff=0.5)
        
        self.play(Create(axes), run_time=1)
        
        # Linea de crecimiento
        graph = axes.plot(lambda x: 20 * x, color=GREEN_C)
        self.play(Create(graph), run_time=2)
        
        # Etiqueta de valor
        value_lbl = Text("+100%", color=GREEN_B, font_size=48).next_to(graph, UP_RIGHT, buff=0.1)
        self.play(FadeIn(value_lbl, shift=UP), run_time=1)
        
        self.wait(1.5)
        
        # Fade out
        self.play(FadeOut(VGroup(title, axes, graph, value_lbl)), run_time=1)
"""

def generate_manim_overlay(
    gancho: str,
    nicho_slug: str,
    temp_dir: Path,
    timestamp: int,
    bg_color: str = "#00FF00"  # We use pure green for chroma key as fallback
) -> Path | None:
    """Run Manim to generate a financial chart overlay."""
    if not settings.enable_manim_animations:
        return None
        
    if nicho_slug != settings.manim_enabled_nichos:
        logger.info(f"Manim skipped: niche '{nicho_slug}' is not '{settings.manim_enabled_nichos}'")
        return None
        
    # Check if manim is installed
    try:
        subprocess.run(["manim", "--version"], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        logger.warning("Manim is not installed or not in PATH. Skipping data animation.")
        return None
        
    logger.info(f"📊 Generating Manim financial chart for {nicho_slug}...")
    
    script_path = temp_dir / f"manim_script_{timestamp}.py"
    output_dir = temp_dir / f"manim_output_{timestamp}"
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Customize the title based on the hook
    title = (gancho[:30] + "...") if len(gancho) > 30 else gancho
    title = re.sub(r"[\r\n\t\\]", " ", title)
    title = title.replace('"', '').replace("'", "")
    
    script_content = MANIM_TEMPLATE.format(bg_color=bg_color, title=title)
    script_path.write_text(script_content, encoding="utf-8")
    
    quality = settings.manim_render_quality  # e.g., 'medium_quality' -> -qm, 'high_quality' -> -qh
    q_flag = "-qm"
    if quality == "low_quality": q_flag = "-ql"
    elif quality == "high_quality": q_flag = "-qh"
    elif quality == "production_quality": q_flag = "-qp"
    
    # Run manim subprocess
    # We use -t for transparent background if possible, but some manim versions
    # have issues with mp4 + transparent (-t creates .mov usually), 
    # so we rely on chroma key via bg_color.
    cmd = [
        "manim", 
        str(script_path.resolve()), 
        "FinancialChart", 
        q_flag,
        "--media_dir", str(output_dir.resolve()),
        "--flush_cache",
        "--disable_caching"
    ]
    
    try:
        t0 = time.time()
        result = subprocess.run(
            cmd, 
            capture_output=True, 
            text=True, 
            timeout=settings.manim_timeout_seconds
        )
        if result.returncode != 0:
            logger.error(f"Manim failed: {result.stderr[-500:]}")
            return None
            
        logger.info(f"✅ Manim animation completed in {int(time.time() - t0)}s")
        
        # Fin the rendered video
        # Default path is usually: output_dir/videos/manim_script_*/1080p60/FinancialChart.mp4
        videos_dir = output_dir / "videos"
        for ext in ["mp4", "mov", "webm"]:
            for f in videos_dir.rglob(f"*.{ext}"):
                if f.is_file() and f.name.startswith("FinancialChart"):
                    logger.debug(f"Manim video found: {f}")
                    return f
                    
        logger.warning("Manim completed but no output file was found.")
        return None
        
    except subprocess.TimeoutExpired:
        logger.error(f"Manim timed out after {settings.manim_timeout_seconds}s")
        return None
    except Exception as e:
        logger.error(f"Unexpected error running Manim: {e}")
        return None
