"""Integrations module for connecting external tools with Video Factory."""

from .openmontage_bridge import OpenMontageBridge, bridge

__all__ = ["OpenMontageBridge", "bridge"]
