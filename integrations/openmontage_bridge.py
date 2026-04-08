"""OpenMontage Integration Bridge for Video Factory V16.

Connects OpenMontage tools, styles, and skills with the Video Factory pipeline
for enhanced subtitle synchronization, style management, and video composition.

Usage:
    from integrations.openmontage_bridge import OpenMontageBridge
    
    bridge = OpenMontageBridge()
    
    # Generate word-level synchronized subtitles
    subtitles = bridge.generate_subtitles(
        segments=transcript_segments,
        highlight_style="word_by_word",
        output_path="subtitles.srt"
    )
    
    # Load and apply a style playbook
    style = bridge.load_style("anime-ghibli")
    
    # Burn captions with Remotion
    result = bridge.burn_captions_remotion(
        video_path="input.mp4",
        output_path="output.mp4",
        segments=segments,
        style=style
    )
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

# Import OpenMontage components
from tools.subtitle.subtitle_gen import SubtitleGen
from tools.video.remotion_caption_burn import RemotionCaptionBurn
from tools.analysis.transcriber import Transcriber
from tools.tool_registry import registry
from styles.playbook_loader import (
    load_playbook,
    list_playbooks,
    validate_accessibility,
    validate_palette,
)


class OpenMontageBridge:
    """Bridge between OpenMontage tools and Video Factory V16."""
    
    def __init__(self):
        self.subtitle_gen = SubtitleGen()
        self.caption_burn = RemotionCaptionBurn()
        self.transcriber = Transcriber()
        self._discover_tools()
    
    def _discover_tools(self) -> None:
        """Register all available tools in the registry."""
        registry.register(self.subtitle_gen)
        registry.register(self.caption_burn)
        registry.register(self.transcriber)
    
    def generate_subtitles(
        self,
        segments: list[dict],
        output_path: str | Path,
        format: str = "srt",
        max_words_per_cue: int = 4,
        max_chars_per_line: int = 20,
        highlight_style: str = "word_by_word",
        corrections: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Generate synchronized subtitles from transcript segments.
        
        Args:
            segments: Word-level transcript segments from transcriber
            output_path: Where to save the subtitle file
            format: Output format (srt, vtt, json)
            max_words_per_cue: Maximum words per subtitle cue
            max_chars_per_line: Maximum characters per line
            highlight_style: Highlight style (none, word_by_word, karaoke)
            corrections: Optional word corrections dict
            
        Returns:
            ToolResult data dict with subtitle info
        """
        result = self.subtitle_gen.execute({
            "segments": segments,
            "format": format,
            "output_path": str(output_path),
            "max_words_per_cue": max_words_per_cue,
            "max_chars_per_line": max_chars_per_line,
            "highlight_style": highlight_style,
            "corrections": corrections or {},
        })
        
        return {
            "success": result.success,
            "output": result.data.get("output"),
            "cue_count": result.data.get("cue_count"),
            "format": result.data.get("format"),
            "error": result.error,
        }
    
    def transcribe_with_word_timestamps(
        self,
        audio_path: str | Path,
        output_dir: str | Path | None = None,
        model_size: str = "base",
        language: str | None = None,
    ) -> dict[str, Any]:
        """Transcribe audio with word-level timestamps.
        
        Args:
            audio_path: Path to audio/video file
            output_dir: Directory for output files
            model_size: Whisper model size (tiny, base, small, medium, large-v2, large-v3)
            language: ISO 639-1 language code or None for auto-detect
            
        Returns:
            Dict with segments, word_timestamps, language, duration
        """
        result = self.transcriber.execute({
            "input_path": str(audio_path),
            "output_dir": str(output_dir) if output_dir else None,
            "model_size": model_size,
            "language": language,
        })
        
        return {
            "success": result.success,
            "segments": result.data.get("segments", []),
            "word_timestamps": result.data.get("word_timestamps", []),
            "language": result.data.get("language"),
            "duration_seconds": result.data.get("duration_seconds"),
            "error": result.error,
        }
    
    def burn_captions_remotion(
        self,
        video_path: str | Path,
        output_path: str | Path,
        segments: list[dict],
        words_per_page: int = 4,
        font_size: int = 52,
        highlight_color: str = "#22D3EE",
        corrections: dict[str, str] | None = None,
        overlays: list[dict] | None = None,
        force_ffmpeg: bool = False,
    ) -> dict[str, Any]:
        """Burn animated captions onto video using Remotion.
        
        Args:
            video_path: Input video path
            output_path: Output video path
            segments: Word-level transcript segments
            words_per_page: Words shown at once
            font_size: Caption font size
            highlight_color: Active word highlight color (hex)
            corrections: Optional word corrections
            overlays: Optional overlay objects
            force_ffmpeg: Force FFmpeg fallback instead of Remotion
            
        Returns:
            Dict with method used, output path, and metadata
        """
        result = self.caption_burn.execute({
            "input_path": str(video_path),
            "output_path": str(output_path),
            "segments": segments,
            "words_per_page": words_per_page,
            "font_size": font_size,
            "highlight_color": highlight_color,
            "corrections": corrections or {},
            "overlays": overlays or [],
            "force_ffmpeg": force_ffmpeg,
        })
        
        return {
            "success": result.success,
            "method": result.data.get("method", "unknown"),
            "output": result.data.get("output"),
            "caption_count": result.data.get("caption_count"),
            "duration_seconds": result.data.get("duration_seconds"),
            "note": result.data.get("note"),
            "error": result.error,
        }
    
    def load_style(self, name: str) -> dict[str, Any]:
        """Load a style playbook by name.
        
        Args:
            name: Playbook name (anime-ghibli, clean-professional, etc.)
            
        Returns:
            Validated playbook dict
        """
        return load_playbook(name)
    
    def list_available_styles(self) -> list[str]:
        """List all available style playbooks."""
        return list_playbooks()
    
    def validate_style(self, playbook: dict) -> dict[str, Any]:
        """Validate a style playbook for accessibility and design rules.
        
        Returns:
            Dict with pass/fail status and issues list
        """
        return validate_accessibility(playbook)
    
    def get_style_colors(self, playbook_name: str) -> dict[str, Any]:
        """Extract color palette from a playbook.
        
        Returns:
            Dict with primary, accent, background, text colors
        """
        playbook = self.load_style(playbook_name)
        palette = playbook.get("visual_language", {}).get("color_palette", {})
        
        return {
            "primary": palette.get("primary", []),
            "accent": palette.get("accent", []),
            "background": palette.get("background", "#FFFFFF"),
            "text": palette.get("text", "#000000"),
            "highlight": palette.get("accent", ["#22D3EE"])[0] if palette.get("accent") else "#22D3EE",
        }
    
    def fix_subtitle_sync_issues(
        self,
        segments: list[dict],
        target_words_per_cue: int = 3,
    ) -> list[dict]:
        """Fix common subtitle synchronization issues.
        
        This addresses the desynchronization problem mentioned in DOCUMENTO_CONTEXTO.
        
        Args:
            segments: Original transcript segments
            target_words_per_cue: Target words per subtitle cue
            
        Returns:
            Optimized segments for better synchronization
        """
        optimized = []
        
        for seg in segments:
            words = seg.get("words", [])
            if not words:
                optimized.append(seg)
                continue
            
            # Ensure each word has proper timestamps
            for word in words:
                if "start" not in word or "end" not in word:
                    # Fallback: estimate from segment timing
                    word["start"] = seg.get("start", 0)
                    word["end"] = seg.get("end", 0)
            
            # Validate timing consistency
            words = sorted(words, key=lambda w: w.get("start", 0))
            
            # Check for overlapping words and fix
            for i in range(1, len(words)):
                prev_end = words[i-1].get("end", 0)
                curr_start = words[i].get("start", 0)
                if curr_start < prev_end:
                    # Fix overlap by adjusting start time
                    words[i]["start"] = prev_end
            
            seg["words"] = words
            optimized.append(seg)
        
        return optimized
    
    def get_available_tools(self) -> list[dict[str, Any]]:
        """List all available tools with their status."""
        return [tool.get_info() for tool in registry.get_available()]


# Singleton instance for easy import
bridge = OpenMontageBridge()


if __name__ == "__main__":
    # Demo usage
    print("OpenMontage Bridge Demo")
    print("=" * 50)
    
    # List available tools
    print("\nAvailable Tools:")
    for tool in bridge.get_available_tools():
        print(f"  - {tool['name']} ({tool['provider']}) - {tool['status']}")
    
    # List available styles
    print("\nAvailable Styles:")
    for style in bridge.list_available_styles():
        print(f"  - {style}")
    
    # Load and validate a style
    print("\nValidating 'anime-ghibli' style:")
    style = bridge.load_style("anime-ghibli")
    validation = bridge.validate_style(style)
    print(f"  Pass: {validation['pass']}")
    print(f"  Errors: {validation['error_count']}")
    print(f"  Warnings: {validation['warning_count']}")
    
    # Get colors
    colors = bridge.get_style_colors("anime-ghibli")
    print(f"\nAnime Ghibli Colors:")
    print(f"  Primary: {colors['primary']}")
    print(f"  Highlight: {colors['highlight']}")
