"""
Playbook System - Load and manage visual design playbooks for each niche.

A playbook is a YAML file containing design tokens, motion rules, typography,
color palettes, and quality criteria for a specific niche (e.g., finanzas, historia).

This module handles loading, validating, and exposing playbook data to agents.
"""

import yaml
import os
from pathlib import Path
from typing import Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)


class PlaybookError(Exception):
    """Base exception for playbook-related errors."""
    pass


class Playbook:
    """Flexible playbook definition that accepts arbitrary YAML structure."""
    
    def __init__(self, data: Dict[str, Any]):
        """
        Initialize from parsed YAML dict.
        
        Args:
            data: Dictionary from YAML file
        """
        self.data = data
        self.name = data.get('name', 'Unknown')
        self.description = data.get('description', '')
        self.mood = data.get('mood', '')
        self.best_for = data.get('best_for', '')
        self.target_audience = data.get('target_audience', '')
        self.identity = data.get('identity', {})
        self.visual_language = data.get('visual_language', {})
        self.typography = data.get('typography', {})
        self.motion = data.get('motion', {})
        self.audio = data.get('audio', {})
        self.asset_generation = data.get('asset_generation', {})
        self.color_harmony = data.get('color_harmony', {})
        self.validation_scoring = data.get('validation_scoring', {})
        self.notes = data.get('notes', None)
    
    def __getitem__(self, key: str) -> Any:
        """Allow dict-like access to playbook data."""
        return self.data.get(key)


class PlaybookLoader:
    """Load and manage playbooks from YAML files."""
    
    def __init__(self, playbook_dir: str = None):
        """
        Initialize loader.
        
        Args:
            playbook_dir: Directory containing playbook YAML files. 
                         Defaults to ./nichos/
        """
        if playbook_dir is None:
            playbook_dir = Path(__file__).parent.parent / "nichos"
        
        self.playbook_dir = Path(playbook_dir)
        self._playbooks: Dict[str, Playbook] = {}
        self._load_all_playbooks()
    
    def _load_all_playbooks(self):
        """Load all playbook files from directory."""
        if not self.playbook_dir.exists():
            logger.warning(f"Playbook directory not found: {self.playbook_dir}")
            return
        
        playbook_files = list(self.playbook_dir.glob("*.playbook.yaml"))
        logger.info(f"Found {len(playbook_files)} playbook files in {self.playbook_dir}")
        
        for playbook_file in playbook_files:
            try:
                self._load_playbook_file(playbook_file)
            except Exception as e:
                logger.error(f"Failed to load playbook {playbook_file}: {e}")
    
    def _load_playbook_file(self, filepath: Path):
        """Load a single playbook YAML file."""
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f)
            
            if not data:
                logger.warning(f"Empty playbook file: {filepath}")
                return
            
            # Create Playbook instance from dict
            playbook = Playbook(data)
            
            # Store by niche name (derived from filename: finanzas.playbook.yaml -> finanzas)
            niche_name = filepath.stem.replace('.playbook', '')
            self._playbooks[niche_name] = playbook
            
            logger.info(f"Loaded playbook: {niche_name} ({playbook.name})")
        
        except yaml.YAMLError as e:
            raise PlaybookError(f"YAML parsing error in {filepath}: {e}")
        except Exception as e:
            raise PlaybookError(f"Failed to load playbook {filepath}: {e}")
    
    def get_playbook(self, niche: str) -> Optional[Playbook]:
        """
        Get playbook for a niche.
        
        Args:
            niche: Niche name (e.g., 'finanzas', 'historia')
        
        Returns:
            Playbook object or None if not found
        """
        if niche not in self._playbooks:
            logger.warning(f"Playbook not found for niche: {niche}")
            return None
        
        return self._playbooks[niche]
    
    def get_color_palette(self, niche: str) -> Optional[Dict[str, Any]]:
        """Get color palette for a niche."""
        playbook = self.get_playbook(niche)
        if playbook:
            return playbook.visual_language.get('primary_colors', {})
        return None
    
    def get_motion_rules(self, niche: str) -> Optional[Dict[str, Any]]:
        """Get motion/pacing rules for a niche."""
        playbook = self.get_playbook(niche)
        if playbook:
            return playbook.motion
        return None
    
    def get_quality_rules(self, niche: str) -> Optional[list]:
        """Get quality validation rules for a niche."""
        playbook = self.get_playbook(niche)
        if playbook:
            return playbook.asset_generation.get('quality_rules', [])
        return None
    
    def list_available_playbooks(self) -> Dict[str, str]:
        """List all available playbooks with their descriptions."""
        return {
            niche: playbook.description 
            for niche, playbook in self._playbooks.items()
        }
    
    def validate_asset_against_playbook(self, niche: str, asset_metadata: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validate an asset against playbook quality rules.
        
        Args:
            niche: Niche name
            asset_metadata: Asset metadata (colors, dimensions, etc.)
        
        Returns:
            Validation result with score and recommendations
        """
        playbook = self.get_playbook(niche)
        if not playbook:
            return {'valid': None, 'score': 0, 'issues': ['Playbook not found']}
        
        quality_rules = playbook.asset_generation.get('quality_rules', [])
        issues = []
        passed_rules = 0
        
        for rule in quality_rules:
            rule_text = rule.get('rule', '') if isinstance(rule, dict) else str(rule)
            # TODO: Implement actual validation logic per rule type
            # For now, this is a placeholder
            logger.debug(f"Checking rule: {rule_text}")
        
        score = (passed_rules / len(quality_rules)) * 10 if quality_rules else 10
        acceptable_score = playbook.validation_scoring.get('acceptable_score', 8.0)
        
        return {
            'valid': score >= acceptable_score,
            'score': score,
            'acceptable_score': acceptable_score,
            'issues': issues
        }


# Global singleton instance
_playbook_loader: Optional[PlaybookLoader] = None


def get_playbook_loader(playbook_dir: str = None) -> PlaybookLoader:
    """Get or create global playbook loader."""
    global _playbook_loader
    if _playbook_loader is None:
        _playbook_loader = PlaybookLoader(playbook_dir)
    return _playbook_loader


def get_playbook(niche: str) -> Optional[Playbook]:
    """Convenience function to get a playbook."""
    loader = get_playbook_loader()
    return loader.get_playbook(niche)


if __name__ == "__main__":
    # Test the loader
    loader = PlaybookLoader()
    available = loader.list_available_playbooks()
    print("\nAvailable Playbooks:")
    for niche, description in available.items():
        print(f"  {niche}: {description}")
    
    # Test getting a playbook
    finanzas_pb = loader.get_playbook('finanzas')
    if finanzas_pb:
        print(f"\nLoaded: {finanzas_pb.name}")
        print(f"Energy: {finanzas_pb.identity.get('energy_level', 'N/A')}")
        
        # Handle both list and dict formats for primary_colors
        colors = finanzas_pb.visual_language.get('primary_colors', [])
        if isinstance(colors, list):
            color_names = [c.get('name', 'unknown') for c in colors if isinstance(c, dict)]
            print(f"Colors: {color_names}")
        else:
            print(f"Colors: {list(colors.keys())}")
