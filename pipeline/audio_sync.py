"""Video Factory V16 — Audio Synchronization & Subtitle Alignment.

Forces alignment between script text and audio using WhisperX.
Generates accurate timestamps for subtitles even when TTS doesn't provide them.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from loguru import logger


class AudioSubtitleSynchronizer:
    """Synchronize script text with audio timing.
    
    Uses WhisperX forced alignment to generate word-level timestamps.
    Solves the problem of TTS engines that don't provide timing data.
    
    Usage:
        sync = AudioSubtitleSynchronizer()
        
        # Align script with audio
        sync.align_script_with_audio(
            audio_path="audio.mp3",
            script_text="Texto completo del guion...",
            output_vtt="subtitles.vtt"
        )
    """
    
    def __init__(self, model_size: str = "base"):
        """Initialize synchronizer.
        
        Args:
            model_size: Whisper model size (tiny, base, small, medium, large)
                       Larger = more accurate but slower and more RAM
        """
        self.model_size = model_size
        self._whisperx_available = self._check_whisperx()
    
    def _check_whisperx(self) -> bool:
        """Check if WhisperX is installed."""
        try:
            import importlib
            importlib.import_module("whisperx")
            logger.info("✅ WhisperX available for audio alignment")
            return True
        except ImportError:
            logger.warning("⚠️ WhisperX not installed. Alignment will use fallback.")
            logger.info("   Install: pip install whisperx")
            return False
    
    def align_script_with_audio(
        self,
        audio_path: Path,
        script_text: str,
        output_vtt: Path,
        language: str = "es"
    ) -> bool:
        """Force-align script text with audio to generate accurate timestamps.
        
        This is the main method that generates properly synchronized subtitles.
        
        Args:
            audio_path: Path to audio file (mp3, wav, etc.)
            script_text: The original script text
            output_vtt: Output path for VTT file
            language: Language code (es, en, etc.)
            
        Returns:
            True if successful, False otherwise
        """
        if not self._whisperx_available:
            return self._fallback_alignment(audio_path, script_text, output_vtt)
        
        try:
            return self._whisperx_alignment(audio_path, script_text, output_vtt, language)
        except Exception as e:
            logger.error(f"WhisperX alignment failed: {e}")
            return self._fallback_alignment(audio_path, script_text, output_vtt)
    
    def _whisperx_alignment(
        self,
        audio_path: Path,
        script_text: str,
        output_vtt: Path,
        language: str
    ) -> bool:
        """Use WhisperX for high-quality forced alignment."""
        import whisperx
        import torch
        
        logger.info(f"🔍 Starting WhisperX alignment with {self.model_size} model")
        
        # Load model
        device = "cuda" if torch.cuda.is_available() else "cpu"
        compute_type = "float16" if device == "cuda" else "int8"
        
        model = whisperx.load_model(
            self.model_size,
            device,
            compute_type=compute_type,
            language=language
        )
        
        # Load audio
        audio = whisperx.load_audio(str(audio_path))
        
        # 1. Transcribe to get segments
        result = model.transcribe(audio, batch_size=16)
        
        # 2. Load alignment model
        align_model, align_metadata = whisperx.load_align_model(
            language_code=language,
            device=device
        )
        
        # 3. Align segments
        aligned = whisperx.align(
            result["segments"],
            align_model,
            align_metadata,
            audio,
            device,
            return_char_alignments=False
        )
        
        # 4. Adjust to match original script text (forced alignment)
        # WhisperX transcribes, but we want to force the original text
        adjusted_segments = self._adjust_to_original_script(
            aligned["segments"],
            script_text
        )
        
        # 5. Generate VTT
        self._generate_vtt(adjusted_segments, output_vtt)
        
        logger.info(f"✅ Alignment complete: {len(adjusted_segments)} segments")
        return True
    
    def _adjust_to_original_script(
        self,
        whisper_segments: list[dict],
        original_script: str
    ) -> list[dict]:
        """Adjust WhisperX output to match original script text.
        
        WhisperX transcribes what it hears, but we want the exact original text
        with the timing from the audio.
        """
        # Clean original script
        original_words = self._clean_text(original_script).split()
        
        # Extract all words from WhisperX output with timing
        whisper_words = []
        for seg in whisper_segments:
            for word in seg.get("words", []):
                whisper_words.append({
                    "word": word["word"],
                    "start": word["start"],
                    "end": word["end"],
                    "score": word.get("score", 1.0)
                })
        
        # If lengths don't match, we'll use the Whisper words but mark uncertainty
        if len(original_words) != len(whisper_words):
            logger.warning(
                f"Word count mismatch: original={len(original_words)}, "
                f"detected={len(whisper_words)}"
            )
        
        # Map original words to timing (best effort)
        adjusted = []
        min_len = min(len(original_words), len(whisper_words))
        
        for i in range(min_len):
            adjusted.append({
                "word": original_words[i],
                "start": whisper_words[i]["start"],
                "end": whisper_words[i]["end"],
                "score": whisper_words[i].get("score", 1.0)
            })
        
        # If original has more words, extend timing from last word
        if len(original_words) > len(whisper_words) and whisper_words:
            last_end = whisper_words[-1]["end"]
            avg_duration = self._estimate_avg_word_duration(whisper_words)
            
            for i in range(min_len, len(original_words)):
                start = last_end + (i - min_len) * avg_duration
                end = start + avg_duration
                adjusted.append({
                    "word": original_words[i],
                    "start": start,
                    "end": end,
                    "score": 0.5  # Lower confidence for extrapolated
                })
        
        # Group into segments (phrases)
        return self._group_into_segments(adjusted)
    
    def _estimate_avg_word_duration(self, words: list[dict]) -> float:
        """Estimate average word duration from aligned words."""
        if not words:
            return 0.3  # Default 300ms per word
        
        durations = [w["end"] - w["start"] for w in words]
        return sum(durations) / len(durations)
    
    def _group_into_segments(self, words: list[dict], max_words_per_seg: int = 5) -> list[dict]:
        """Group words into segments for VTT."""
        segments = []
        current_seg = []
        
        for word in words:
            current_seg.append(word)
            
            # Break segment on punctuation or max words
            if word["word"].rstrip().endswith((",", ".", "!", "?")) or len(current_seg) >= max_words_per_seg:
                segments.append({
                    "text": " ".join(w["word"] for w in current_seg),
                    "start": current_seg[0]["start"],
                    "end": current_seg[-1]["end"],
                    "words": current_seg
                })
                current_seg = []
        
        # Add remaining words
        if current_seg:
            segments.append({
                "text": " ".join(w["word"] for w in current_seg),
                "start": current_seg[0]["start"],
                "end": current_seg[-1]["end"],
                "words": current_seg
            })
        
        return segments
    
    def _generate_vtt(self, segments: list[dict], output_path: Path) -> None:
        """Generate WebVTT file from segments."""
        lines = ["WEBVTT\n\n"]
        
        for i, seg in enumerate(segments, 1):
            start = self._format_timestamp(seg["start"])
            end = self._format_timestamp(seg["end"])
            text = seg["text"]
            
            lines.append(f"{i}\n")
            lines.append(f"{start} --> {end}\n")
            lines.append(f"{text}\n\n")
        
        output_path.write_text("".join(lines), encoding="utf-8")
        logger.debug(f"Generated VTT: {output_path}")
    
    def _format_timestamp(self, seconds: float) -> str:
        """Format seconds to VTT timestamp (HH:MM:SS.mmm)."""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = seconds % 60
        return f"{hours:02d}:{minutes:02d}:{secs:06.3f}"
    
    def _fallback_alignment(
        self,
        audio_path: Path,
        script_text: str,
        output_vtt: Path
    ) -> bool:
        """Fallback: Estimate timing based on audio duration and word count."""
        try:
            # Get audio duration using ffprobe
            duration = self._get_audio_duration(audio_path)
            
            if not duration:
                logger.error("Could not determine audio duration")
                return False
            
            # Clean script
            words = self._clean_text(script_text).split()
            word_count = len(words)
            
            if word_count == 0:
                logger.error("No words in script")
                return False
            
            # Estimate: average speaking rate ~150 words/minute = 2.5 words/second
            avg_word_duration = duration / word_count
            
            # Generate evenly distributed segments
            segments = []
            seg_size = 4  # words per segment
            
            for i in range(0, len(words), seg_size):
                seg_words = words[i:i+seg_size]
                start_time = i * avg_word_duration
                end_time = min((i + len(seg_words)) * avg_word_duration, duration)
                
                segments.append({
                    "text": " ".join(seg_words),
                    "start": start_time,
                    "end": end_time
                })
            
            self._generate_vtt(segments, output_vtt)
            
            logger.warning(
                f"⚠️ Used fallback alignment (estimation). "
                f"Consider installing WhisperX for better accuracy."
            )
            return True
            
        except Exception as e:
            logger.error(f"Fallback alignment failed: {e}")
            return False
    
    def _get_audio_duration(self, audio_path: Path) -> Optional[float]:
        """Get audio file duration using ffprobe."""
        try:
            result = subprocess.run(
                [
                    "ffprobe",
                    "-v", "error",
                    "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1",
                    str(audio_path)
                ],
                capture_output=True,
                text=True,
                timeout=10
            )
            return float(result.stdout.strip())
        except Exception:
            return None
    
    def _clean_text(self, text: str) -> str:
        """Clean text for processing."""
        # Remove extra whitespace, normalize
        text = " ".join(text.split())
        # Remove special chars that might confuse alignment
        text = text.replace("\n", " ").replace("\r", " ")
        return text.strip()


def align_subtitles_for_job(
    audio_path: Path,
    script_text: str,
    output_vtt: Path,
    use_whisperx: bool = True
) -> bool:
    """Convenience function to align subtitles for a video job.
    
    Args:
        audio_path: Path to generated audio
        script_text: Original script text
        output_vtt: Where to save the VTT
        use_whisperx: Whether to try WhisperX (falls back if unavailable)
        
    Returns:
        True if successful
    """
    if not audio_path.exists():
        logger.error(f"Audio file not found: {audio_path}")
        return False
    
    synchronizer = AudioSubtitleSynchronizer(
        model_size="base" if use_whisperx else "none"
    )
    
    return synchronizer.align_script_with_audio(
        audio_path=audio_path,
        script_text=script_text,
        output_vtt=output_vtt
    )
