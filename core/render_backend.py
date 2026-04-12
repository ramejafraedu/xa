"""
Render Backend - ViMax Integration
Backend abstracto para renderizado de video con múltiples providers
"""

from __future__ import annotations

import json
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Any

from loguru import logger


class RenderProvider(Enum):
    """Proveedores de renderizado soportados"""
    REMOTION = "remotion"
    FFMPEG = "ffmpeg"
    VELO = "veo"  # Google Veo (futuro)
    MOVIE_PY = "moviepy"


@dataclass
class RenderConfig:
    """Configuración de renderizado"""
    provider: RenderProvider
    width: int = 1080
    height: int = 1920
    fps: int = 30
    video_bitrate: str = "8000k"
    audio_bitrate: str = "192k"
    preset: str = "ultrafast"
    theme: str = "cyberpunk"
    composition: str = "UniversalCommercial"
    timeout_seconds: int = 300


@dataclass
class RenderResult:
    """Resultado del renderizado"""
    success: bool
    output_path: Optional[Path]
    duration_seconds: float
    render_time_seconds: float
    provider: RenderProvider
    error_message: Optional[str] = None
    metadata: Dict[str, Any] = None


class BaseRenderBackend(ABC):
    """Clase base abstracta para backends de renderizado"""
    
    def __init__(self, config: RenderConfig):
        self.config = config
    
    @abstractmethod
    def render(
        self,
        input_data: Dict[str, Any],
        output_path: Path
    ) -> RenderResult:
        """Renderiza el video según la configuración"""
        pass
    
    @abstractmethod
    def validate_input(self, input_data: Dict[str, Any]) -> bool:
        """Valida que los datos de entrada sean correctos"""
        pass
    
    @abstractmethod
    def get_supported_formats(self) -> List[str]:
        """Retorna formatos soportados"""
        pass


class RemotionRenderBackend(BaseRenderBackend):
    """
    Backend de renderizado usando Remotion (principal).
    """
    
    def __init__(self, config: RenderConfig, remotion_dir: Path = None):
        super().__init__(config)
        self.remotion_dir = remotion_dir or Path("remotion-composer")
        
    def render(
        self,
        input_data: Dict[str, Any],
        output_path: Path
    ) -> RenderResult:
        """Renderiza usando Remotion CLI"""
        import time
        
        start_time = time.time()
        
        try:
            # Preparar input props
            input_props = {
                **input_data,
                "theme": self.config.theme,
                "fps": self.config.fps,
                "width": self.config.width,
                "height": self.config.height,
            }
            
            # Guardar props a archivo temporal
            props_path = output_path.parent / f"props_{output_path.stem}.json"
            props_path.write_text(json.dumps(input_props), encoding="utf-8")
            
            # Construir comando Remotion
            cmd = [
                "npx", "remotion", "render",
                str(self.remotion_dir / "src" / "index.tsx"),
                self.config.composition,
                str(output_path),
                "--props", str(props_path),
                "--log=verbose",
                "--timeout", str(self.config.timeout_seconds * 1000),  # ms
            ]
            
            logger.info(f"Iniciando render Remotion: {self.config.composition}")
            logger.debug(f"Comando: {' '.join(cmd)}")
            
            # Ejecutar render
            result = subprocess.run(
                cmd,
                cwd=str(self.remotion_dir),
                capture_output=True,
                text=True,
                timeout=self.config.timeout_seconds
            )
            
            # Calcular duración del video resultante
            duration = self._get_video_duration(output_path) if output_path.exists() else 0
            render_time = time.time() - start_time
            
            if result.returncode == 0 and output_path.exists():
                logger.success(
                    f"Render completado: {output_path.name} "
                    f"({duration:.1f}s en {render_time:.1f}s)"
                )
                
                # Limpiar props temporal
                props_path.unlink(missing_ok=True)
                
                return RenderResult(
                    success=True,
                    output_path=output_path,
                    duration_seconds=duration,
                    render_time_seconds=render_time,
                    provider=RenderProvider.REMOTION,
                    metadata={
                        "composition": self.config.composition,
                        "theme": self.config.theme,
                    }
                )
            else:
                error_msg = result.stderr if result.stderr else "Render failed"
                logger.error(f"Error en render Remotion: {error_msg}")
                
                return RenderResult(
                    success=False,
                    output_path=None,
                    duration_seconds=0,
                    render_time_seconds=time.time() - start_time,
                    provider=RenderProvider.REMOTION,
                    error_message=error_msg
                )
                
        except subprocess.TimeoutExpired:
            logger.error(f"Timeout en render (>{self.config.timeout_seconds}s)")
            return RenderResult(
                success=False,
                output_path=None,
                duration_seconds=0,
                render_time_seconds=self.config.timeout_seconds,
                provider=RenderProvider.REMOTION,
                error_message="Render timeout"
            )
        except Exception as e:
            logger.error(f"Error inesperado en render: {e}")
            return RenderResult(
                success=False,
                output_path=None,
                duration_seconds=0,
                render_time_seconds=time.time() - start_time,
                provider=RenderProvider.REMOTION,
                error_message=str(e)
            )
    
    def validate_input(self, input_data: Dict[str, Any]) -> bool:
        """Valida datos de entrada para Remotion"""
        required = ["script", "title"]
        return all(key in input_data for key in required)
    
    def get_supported_formats(self) -> List[str]:
        return ["mp4", "webm", "mov"]
    
    def _get_video_duration(self, video_path: Path) -> float:
        """Obtiene duración del video resultante"""
        try:
            cmd = [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(video_path)
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            return float(result.stdout.strip())
        except:
            return 0.0


class FFmpegRenderBackend(BaseRenderBackend):
    """
    Backend de renderizado usando FFmpeg (fallback).
    """
    
    def __init__(self, config: RenderConfig):
        super().__init__(config)
    
    def render(
        self,
        input_data: Dict[str, Any],
        output_path: Path
    ) -> RenderResult:
        """Renderiza usando FFmpeg directamente"""
        import time
        
        start_time = time.time()
        
        try:
            # Construir comando FFmpeg
            # Nota: Esto es un stub - en producción necesitarías
            # la lógica completa de concatenación de clips
            
            clips = input_data.get("clips", [])
            audio_path = input_data.get("audio_path")
            
            if not clips:
                return RenderResult(
                    success=False,
                    output_path=None,
                    duration_seconds=0,
                    render_time_seconds=0,
                    provider=RenderProvider.FFMPEG,
                    error_message="No clips provided"
                )
            
            # Crear archivo de lista para concatenación
            list_file = output_path.parent / f"concat_{output_path.stem}.txt"
            with open(list_file, "w") as f:
                for clip in clips:
                    f.write(f"file '{clip}'\n")
            
            # Comando FFmpeg
            cmd = [
                "ffmpeg",
                "-f", "concat",
                "-safe", "0",
                "-i", str(list_file),
            ]
            
            if audio_path:
                cmd.extend(["-i", str(audio_path), "-c:v", "copy", "-c:a", "aac"])
            else:
                cmd.extend(["-c:v", "libx264", "-preset", self.config.preset])
            
            cmd.extend([
                "-b:v", self.config.video_bitrate,
                "-b:a", self.config.audio_bitrate,
                "-r", str(self.config.fps),
                "-s", f"{self.config.width}x{self.config.height}",
                "-pix_fmt", "yuv420p",
                "-y",
                str(output_path)
            ])
            
            logger.info(f"Iniciando render FFmpeg fallback")
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.config.timeout_seconds
            )
            
            # Limpiar
            list_file.unlink(missing_ok=True)
            
            duration = self._get_video_duration(output_path) if output_path.exists() else 0
            render_time = time.time() - start_time
            
            if result.returncode == 0:
                return RenderResult(
                    success=True,
                    output_path=output_path,
                    duration_seconds=duration,
                    render_time_seconds=render_time,
                    provider=RenderProvider.FFMPEG,
                    metadata={"method": "concat"}
                )
            else:
                return RenderResult(
                    success=False,
                    output_path=None,
                    duration_seconds=0,
                    render_time_seconds=render_time,
                    provider=RenderProvider.FFMPEG,
                    error_message=result.stderr
                )
                
        except Exception as e:
            return RenderResult(
                success=False,
                output_path=None,
                duration_seconds=0,
                render_time_seconds=time.time() - start_time,
                provider=RenderProvider.FFMPEG,
                error_message=str(e)
            )
    
    def validate_input(self, input_data: Dict[str, Any]) -> bool:
        return "clips" in input_data and len(input_data["clips"]) > 0
    
    def get_supported_formats(self) -> List[str]:
        return ["mp4", "mov", "avi", "mkv"]
    
    def _get_video_duration(self, video_path: Path) -> float:
        try:
            cmd = [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(video_path)
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            return float(result.stdout.strip())
        except:
            return 0.0


class RenderBackend:
    """
    Fachada para el sistema de renderizado.
    
    Selecciona automáticamente el mejor backend disponible.
    """
    
    def __init__(self, config: RenderConfig):
        self.config = config
        self.backends: Dict[RenderProvider, BaseRenderBackend] = {}
        
        # Inicializar backends
        if config.provider == RenderProvider.REMOTION:
            self.backends[RenderProvider.REMOTION] = RemotionRenderBackend(config)
            self.backends[RenderProvider.FFMPEG] = FFmpegRenderBackend(config)
        else:
            self.backends[config.provider] = self._create_backend(config.provider, config)
    
    def _create_backend(
        self,
        provider: RenderProvider,
        config: RenderConfig
    ) -> BaseRenderBackend:
        if provider == RenderProvider.REMOTION:
            return RemotionRenderBackend(config)
        elif provider == RenderProvider.FFMPEG:
            return FFmpegRenderBackend(config)
        else:
            raise ValueError(f"Provider no soportado: {provider}")
    
    def render(
        self,
        input_data: Dict[str, Any],
        output_path: Path,
        prefer_primary: bool = True
    ) -> RenderResult:
        """
        Renderiza intentando primario, luego fallback.
        
        Args:
            input_data: Datos del video (script, clips, audio, etc.)
            output_path: Ruta de salida
            prefer_primary: Si True, intenta primario primero
            
        Returns:
            RenderResult con el resultado
        """
        primary = self.config.provider
        fallback = RenderProvider.FFMPEG if primary != RenderProvider.FFMPEG else None
        
        # Intentar primario
        if prefer_primary and primary in self.backends:
            backend = self.backends[primary]
            if backend.validate_input(input_data):
                result = backend.render(input_data, output_path)
                if result.success:
                    return result
                logger.warning(f"{primary.value} falló, intentando fallback")
        
        # Intentar fallback
        if fallback and fallback in self.backends:
            backend = self.backends[fallback]
            if backend.validate_input(input_data):
                logger.info(f"Usando fallback: {fallback.value}")
                return backend.render(input_data, output_path)
        
        # Si no hay fallback o también falló
        return RenderResult(
            success=False,
            output_path=None,
            duration_seconds=0,
            render_time_seconds=0,
            provider=primary,
            error_message="All render backends failed"
        )
    
    def validate_all_inputs(self, input_data: Dict[str, Any]) -> Dict[RenderProvider, bool]:
        """Valida datos contra todos los backends disponibles"""
        return {
            provider: backend.validate_input(input_data)
            for provider, backend in self.backends.items()
        }


# Helper para uso en pipeline
def render_with_fallback(
    input_data: Dict[str, Any],
    output_path: Path,
    primary_provider: str = "remotion",
    **config_overrides
) -> RenderResult:
    """
    Función helper para renderizar con fallback automático.
    
    Args:
        input_data: Datos del video
        output_path: Ruta de salida
        primary_provider: "remotion" o "ffmpeg"
        **config_overrides: Overrides para RenderConfig
        
    Returns:
        RenderResult
    """
    provider = RenderProvider.REMOTION if primary_provider == "remotion" else RenderProvider.FFMPEG
    
    config = RenderConfig(
        provider=provider,
        **config_overrides
    )
    
    backend = RenderBackend(config)
    return backend.render(input_data, output_path)
