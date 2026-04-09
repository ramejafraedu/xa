"""Video Factory V16 — Memory Budget Manager.

Enforces RAM usage limits per video job to prevent system overload.
Implements streaming processing and frame buffering limits.
"""
from __future__ import annotations

import gc
import os
import tempfile
from pathlib import Path
from typing import Optional

import psutil
from loguru import logger


class MemoryBudgetManager:
    """Manage memory usage for video processing jobs.
    
    Enforces a maximum RAM percentage per job (default 20%).
    Implements chunk-based processing and automatic GC.
    
    Usage:
        manager = MemoryBudgetManager(max_percent=20.0)
        
        # Check before heavy allocation
        if manager.can_allocate(bytes_needed=500_000_000):
            process_large_chunk()
        else:
            # Use smaller chunks or disk streaming
            process_in_smaller_chunks()
    """
    
    def __init__(self, max_percent: float = 20.0, job_id: str = ""):
        """Initialize memory budget manager.
        
        Args:
            max_percent: Maximum percentage of total RAM this job can use
            job_id: Optional job identifier for logging
        """
        self.max_percent = max_percent
        self.job_id = job_id
        self.total_ram = psutil.virtual_memory().total
        self.max_bytes = int(self.total_ram * (max_percent / 100))
        
        # Tracking
        self.peak_usage = 0
        self.allocations: list[tuple[str, int]] = []  # (description, bytes)
        
        # Buffer limits
        self.frame_buffer_seconds = 30  # Max seconds of frames in RAM
        self.use_disk_streaming = True
        self.temp_dir: Optional[Path] = None
        
        # Calculate safe limits
        self._calculate_safe_limits()
        
        logger.info(
            f"🧠 Memory Budget initialized: "
            f"{self._human_readable(self.max_bytes)} / "
            f"{self._human_readable(self.total_ram)} ({max_percent}%)"
        )
    
    def _calculate_safe_limits(self) -> None:
        """Calculate safe processing limits based on available RAM."""
        # Frame size estimation (1080x1920 RGB = ~6MB per frame)
        frame_size_bytes = 1920 * 1080 * 3
        
        # Max frames we can safely hold in RAM
        # Leave 30% headroom for other allocations
        safe_ram = int(self.max_bytes * 0.7)
        self.max_frames_in_ram = max(1, safe_ram // frame_size_bytes)
        
        # At 30fps, how many seconds of video can we buffer?
        self.max_buffer_seconds = self.max_frames_in_ram // 30
        
        # Don't exceed configured limit
        self.max_buffer_seconds = min(self.max_buffer_seconds, self.frame_buffer_seconds)
        
        logger.debug(
            f"Buffer limits: {self.max_frames_in_ram} frames = "
            f"{self.max_buffer_seconds}s at 30fps"
        )
    
    def _human_readable(self, bytes_val: int) -> str:
        """Convert bytes to human readable string."""
        for unit in ['B', 'KB', 'MB', 'GB']:
            if bytes_val < 1024:
                return f"{bytes_val:.1f}{unit}"
            bytes_val /= 1024
        return f"{bytes_val:.1f}TB"
    
    def get_current_usage(self) -> int:
        """Get current memory usage of this process."""
        process = psutil.Process(os.getpid())
        return process.memory_info().rss
    
    def get_available_budget(self) -> int:
        """Get remaining memory budget for this job."""
        current = self.get_current_usage()
        available = max(0, self.max_bytes - current)
        
        # Also check system-wide available RAM
        system_available = psutil.virtual_memory().available
        
        # Return the more conservative estimate
        return min(available, int(system_available * 0.8))
    
    def can_allocate(self, bytes_needed: int, description: str = "") -> bool:
        """Check if allocating bytes_needed stays within budget.
        
        Args:
            bytes_needed: Number of bytes to allocate
            description: Description of what is being allocated (for logging)
            
        Returns:
            True if allocation is safe, False otherwise
        """
        available = self.get_available_budget()
        can_alloc = bytes_needed <= available
        
        if not can_alloc and description:
            logger.warning(
                f"⚠️ Cannot allocate {self._human_readable(bytes_needed)} for {description}: "
                f"only {self._human_readable(available)} available"
            )
        
        return can_alloc
    
    def get_safe_chunk_size(self, video_duration: float, fps: float = 30.0) -> int:
        """Calculate safe number of frames to process in one chunk.
        
        Args:
            video_duration: Total video duration in seconds
            fps: Frames per second
            
        Returns:
            Number of frames that can be safely processed in one batch
        """
        total_frames = int(video_duration * fps)
        
        # Don't buffer more than max_buffer_seconds at a time
        max_chunk_frames = int(self.max_buffer_seconds * fps)
        
        # For very long videos, use even smaller chunks
        if video_duration > 120:  # > 2 minutes
            max_chunk_frames = min(max_chunk_frames, int(10 * fps))  # 10 second chunks
        
        return min(total_frames, max_chunk_frames)
    
    def register_allocation(self, description: str, bytes_allocated: int) -> None:
        """Register a memory allocation for tracking."""
        self.allocations.append((description, bytes_allocated))
        current = self.get_current_usage()
        self.peak_usage = max(self.peak_usage, current)
        
        # Log if we're getting close to limit
        usage_percent = (current / self.max_bytes) * 100
        if usage_percent > 80:
            logger.warning(
                f"🚨 High memory usage: {usage_percent:.1f}% of budget "
                f"({self._human_readable(current)} / {self._human_readable(self.max_bytes)})"
            )
    
    def force_garbage_collection(self) -> int:
        """Force garbage collection and return freed bytes.
        
        Returns:
            Bytes freed by GC
        """
        before = self.get_current_usage()
        gc.collect()
        after = self.get_current_usage()
        freed = max(0, before - after)
        
        if freed > 10_000_000:  # Log if > 10MB freed
            logger.info(f"🧹 GC freed {self._human_readable(freed)}")
        
        return freed
    
    def get_temp_dir(self) -> Path:
        """Get temporary directory for disk-based streaming.
        
        Returns:
            Path to temp directory (created if needed)
        """
        if self.temp_dir is None:
            self.temp_dir = Path(tempfile.mkdtemp(prefix=f"vf_memory_{self.job_id}_"))
            logger.debug(f"Created temp streaming dir: {self.temp_dir}")
        return self.temp_dir
    
    def should_use_disk_streaming(self, bytes_needed: int) -> bool:
        """Determine if operation should use disk streaming instead of RAM.
        
        Args:
            bytes_needed: Estimated bytes needed for operation
            
        Returns:
            True if should use disk streaming
        """
        if not self.use_disk_streaming:
            return False
        
        # Use disk if we can't safely fit in RAM
        if not self.can_allocate(bytes_needed):
            return True
        
        # Also use disk if we're already using > 50% of budget
        current = self.get_current_usage()
        if current > (self.max_bytes * 0.5):
            return True
        
        return False
    
    def cleanup(self) -> None:
        """Cleanup temporary resources."""
        # Force GC
        self.force_garbage_collection()
        
        # Remove temp directory
        if self.temp_dir and self.temp_dir.exists():
            import shutil
            try:
                shutil.rmtree(self.temp_dir)
                logger.debug(f"Cleaned up temp dir: {self.temp_dir}")
            except Exception as e:
                logger.warning(f"Failed to cleanup temp dir: {e}")
        
        # Log final stats
        final_usage = self.get_current_usage()
        logger.info(
            f"🧠 Memory stats for {self.job_id}: "
            f"Peak: {self._human_readable(self.peak_usage)}, "
            f"Final: {self._human_readable(final_usage)}"
        )
    
    def get_report(self) -> dict:
        """Generate memory usage report."""
        current = self.get_current_usage()
        
        # Group allocations by description
        allocation_summary: dict[str, int] = {}
        for desc, bytes_val in self.allocations:
            allocation_summary[desc] = allocation_summary.get(desc, 0) + bytes_val
        
        return {
            "job_id": self.job_id,
            "max_budget": self.max_bytes,
            "max_budget_human": self._human_readable(self.max_bytes),
            "peak_usage": self.peak_usage,
            "peak_usage_human": self._human_readable(self.peak_usage),
            "current_usage": current,
            "current_usage_human": self._human_readable(current),
            "utilization_percent": (self.peak_usage / self.max_bytes) * 100,
            "max_buffer_seconds": self.max_buffer_seconds,
            "allocations": allocation_summary,
            "disk_streaming_used": self.temp_dir is not None
        }


class MemoryEfficientVideoProcessor:
    """Process video frames with memory constraints.
    
    Automatically chunks processing to stay within RAM budget.
    """
    
    def __init__(self, memory_manager: MemoryBudgetManager):
        self.mm = memory_manager
    
    def process_video_frames(
        self,
        input_path: Path,
        output_path: Path,
        processor_func,
        fps: float = 30.0
    ) -> bool:
        """Process video in memory-efficient chunks.
        
        Args:
            input_path: Input video path
            output_path: Output video path
            processor_func: Function to apply to each frame
            fps: Frames per second
            
        Returns:
            True if successful
        """
        import cv2
        
        # Open video
        cap = cv2.VideoCapture(str(input_path))
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        video_duration = total_frames / fps
        
        # Get safe chunk size
        chunk_size = self.mm.get_safe_chunk_size(video_duration, fps)
        
        logger.info(
            f"Processing {total_frames} frames in chunks of {chunk_size} "
            f"(~{chunk_size/fps:.1f}s each)"
        )
        
        # Setup output writer
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        writer = cv2.VideoWriter(str(output_path), fourcc, fps, (width, height))
        
        frames_buffer = []
        frame_count = 0
        
        try:
            while True:
                ret, frame = cap.read()
                if not ret:
                    break
                
                frames_buffer.append(frame)
                frame_count += 1
                
                # Process chunk when buffer is full
                if len(frames_buffer) >= chunk_size:
                    self._process_chunk(frames_buffer, writer, processor_func)
                    frames_buffer = []
                    
                    # Force GC between chunks
                    self.mm.force_garbage_collection()
                    
                    logger.debug(f"Processed {frame_count}/{total_frames} frames")
            
            # Process remaining frames
            if frames_buffer:
                self._process_chunk(frames_buffer, writer, processor_func)
            
            logger.info(f"✅ Video processing complete: {frame_count} frames")
            return True
            
        except Exception as e:
            logger.error(f"Video processing failed: {e}")
            return False
            
        finally:
            cap.release()
            writer.release()
            cv2.destroyAllWindows()
    
    def _process_chunk(self, frames: list, writer, processor_func) -> None:
        """Process a chunk of frames."""
        for frame in frames:
            processed = processor_func(frame)
            writer.write(processed)


# Convenience functions
def create_memory_manager(job_id: str = "", max_percent: Optional[float] = None) -> MemoryBudgetManager:
    """Create a memory manager with default settings."""
    from config import settings
    
    percent = max_percent or getattr(settings, 'max_ram_percent_per_job', 20.0)
    return MemoryBudgetManager(max_percent=percent, job_id=job_id)
