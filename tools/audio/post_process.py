"""
Audio Post-Processing - SaarD00 Integration
Silence trimming, volume boost, normalization, noise reduction
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from loguru import logger


@dataclass
class AudioProcessResult:
    """Resultado del procesamiento de audio"""
    output_path: Path
    original_duration: float
    processed_duration: float
    silence_removed: float
    volume_increase_db: float
    success: bool
    error: Optional[str] = None


class AudioPostProcessor:
    """
    Post-procesador de audio basado en AI-Youtube-Shorts-Generator (SaarD00).
    
    Features:
    - Silence trimming (eliminación de silencios)
    - Volume boost (aumento de volumen)
    - Normalization (normalización de audio)
    - Noise reduction (reducción de ruido)
    """
    
    def __init__(self, ffmpeg_path: str = "ffmpeg"):
        self.ffmpeg_path = ffmpeg_path
        
    def process(
        self,
        input_path: Path,
        output_path: Path,
        silence_threshold: float = -50,  # dB
        min_silence_duration: float = 0.3,  # seconds
        volume_boost: float = 1.2,  # multiplier
        normalize: bool = True,
        noise_reduction: bool = False
    ) -> AudioProcessResult:
        """
        Procesa audio aplicando todas las optimizaciones.
        
        Args:
            input_path: Archivo de audio de entrada
            output_path: Archivo de salida
            silence_threshold: Umbral para detectar silencio (dB)
            min_silence_duration: Duración mínima de silencio a eliminar
            volume_boost: Multiplicador de volumen (1.0 = sin cambio)
            normalize: Normalizar audio
            noise_reduction: Aplicar reducción de ruido
            
        Returns:
            AudioProcessResult con estadísticas del procesamiento
        """
        try:
            # Obtener duración original
            original_duration = self._get_duration(input_path)
            
            # Construir filtro FFmpeg
            filters = []
            
            # 1. Silenceremove (eliminar silencios)
            silence_filter = (
                f"silenceremove=stop_periods=-1:"
                f"stop_duration={min_silence_duration}:"
                f"stop_threshold={silence_threshold}dB"
            )
            filters.append(silence_filter)
            
            # 2. Volume boost
            if volume_boost != 1.0:
                filters.append(f"volume={volume_boost}")
            
            # 3. Normalization (loudnorm)
            if normalize:
                filters.append("loudnorm=I=-16:TP=-1.5:LRA=11")
            
            # 4. Noise reduction (arnndn o afftdn)
            if noise_reduction:
                # Simple noise reduction usando afftdn
                filters.append("afftdn=nf=-25")
            
            # Construir comando FFmpeg
            filter_complex = ",".join(filters)
            
            cmd = [
                self.ffmpeg_path,
                "-i", str(input_path),
                "-af", filter_complex,
                "-c:a", "aac",
                "-b:a", "192k",
                "-y",  # Overwrite output
                str(output_path)
            ]
            
            logger.info(f"Procesando audio: {input_path.name}")
            logger.debug(f"FFmpeg filter: {filter_complex}")
            
            # Ejecutar FFmpeg
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True
            )
            
            # Obtener duración procesada
            processed_duration = self._get_duration(output_path)
            silence_removed = original_duration - processed_duration
            
            # Calcular aumento de volumen en dB
            volume_increase_db = 20 * (volume_boost - 1.0) if volume_boost > 1.0 else 0
            
            logger.info(
                f"Audio procesado: {original_duration:.2f}s -> {processed_duration:.2f}s "
                f"(silencio removido: {silence_removed:.2f}s)"
            )
            
            return AudioProcessResult(
                output_path=output_path,
                original_duration=original_duration,
                processed_duration=processed_duration,
                silence_removed=silence_removed,
                volume_increase_db=volume_increase_db,
                success=True
            )
            
        except subprocess.CalledProcessError as e:
            logger.error(f"Error procesando audio: {e.stderr}")
            return AudioProcessResult(
                output_path=output_path,
                original_duration=0,
                processed_duration=0,
                silence_removed=0,
                volume_increase_db=0,
                success=False,
                error=str(e.stderr)
            )
        except Exception as e:
            logger.error(f"Error inesperado: {e}")
            return AudioProcessResult(
                output_path=output_path,
                original_duration=0,
                processed_duration=0,
                silence_removed=0,
                volume_increase_db=0,
                success=False,
                error=str(e)
            )
    
    def trim_silence_only(
        self,
        input_path: Path,
        output_path: Path,
        threshold: float = -50,
        min_duration: float = 0.3
    ) -> AudioProcessResult:
        """Solo elimina silencios sin otros procesamientos"""
        return self.process(
            input_path=input_path,
            output_path=output_path,
            silence_threshold=threshold,
            min_silence_duration=min_duration,
            volume_boost=1.0,
            normalize=False,
            noise_reduction=False
        )
    
    def normalize_only(
        self,
        input_path: Path,
        output_path: Path,
        target_lufs: float = -16
    ) -> AudioProcessResult:
        """Solo normaliza el audio a nivel LUFS objetivo"""
        # Override con loudnorm específico
        try:
            original_duration = self._get_duration(input_path)
            
            cmd = [
                self.ffmpeg_path,
                "-i", str(input_path),
                "-af", f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11",
                "-c:a", "aac",
                "-b:a", "192k",
                "-y",
                str(output_path)
            ]
            
            subprocess.run(cmd, capture_output=True, text=True, check=True)
            processed_duration = self._get_duration(output_path)
            
            return AudioProcessResult(
                output_path=output_path,
                original_duration=original_duration,
                processed_duration=processed_duration,
                silence_removed=0,
                volume_increase_db=0,
                success=True
            )
            
        except Exception as e:
            return AudioProcessResult(
                output_path=output_path,
                original_duration=0,
                processed_duration=0,
                silence_removed=0,
                volume_increase_db=0,
                success=False,
                error=str(e)
            )
    
    def _get_duration(self, audio_path: Path) -> float:
        """Obtiene la duración de un archivo de audio usando ffprobe"""
        try:
            cmd = [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(audio_path)
            ]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True
            )
            
            return float(result.stdout.strip())
            
        except:
            return 0.0


# Helper functions para uso en pipeline
def process_video_audio(
    video_path: Path,
    output_path: Path,
    **kwargs
) -> AudioProcessResult:
    """
    Procesa el audio de un video directamente.
    
    Extrae el audio, lo procesa y lo recombina con el video.
    """
    processor = AudioPostProcessor()
    
    # Archivo temporal para audio procesado
    temp_audio = output_path.parent / f"temp_audio_{video_path.stem}.aac"
    
    # Extraer audio
    extract_cmd = [
        "ffmpeg",
        "-i", str(video_path),
        "-vn",  # No video
        "-acodec", "copy",
        str(temp_audio)
    ]
    
    try:
        subprocess.run(extract_cmd, capture_output=True, check=True)
        
        # Procesar audio
        result = processor.process(temp_audio, temp_audio, **kwargs)
        
        if result.success:
            # Recombinar audio procesado con video original
            combine_cmd = [
                "ffmpeg",
                "-i", str(video_path),
                "-i", str(temp_audio),
                "-c:v", "copy",  # Copiar video sin re-encode
                "-c:a", "aac",
                "-b:a", "192k",
                "-map", "0:v:0",  # Video del original
                "-map", "1:a:0",  # Audio procesado
                "-shortest",
                "-y",
                str(output_path)
            ]
            
            subprocess.run(combine_cmd, capture_output=True, check=True)
            
            # Limpiar temporal
            temp_audio.unlink(missing_ok=True)
            
            logger.info(f"Video procesado con audio optimizado: {output_path}")
        
        return result
        
    except Exception as e:
        logger.error(f"Error procesando video: {e}")
        # Limpiar temporal si existe
        temp_audio.unlink(missing_ok=True)
        
        return AudioProcessResult(
            output_path=output_path,
            original_duration=0,
            processed_duration=0,
            silence_removed=0,
            volume_increase_db=0,
            success=False,
            error=str(e)
        )
