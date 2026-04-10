"""YAML Niche Loader — V16 upgrade.

Loads all .yaml files from the /nichos/ directory and converts them to
NichoConfig objects. Falls back to the hardcoded dict if YAML is unavailable
or files are missing.

Validation errors (e.g. ``num_clips: "ocho"``) are caught at load time
and logged with full Pydantic context so they never surface as a mystery
crash deep inside Stage 6.

Usage (replaces the bottom of config.py):
    from nichos._loader import load_nichos_from_yaml_dir
    NICHOS = load_nichos_from_yaml_dir(NICHOS_HARDCODED)
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from models.config_models import NichoConfig

_NICHOS_DIR = Path(__file__).resolve().parent


def load_nichos_from_yaml_dir(
    base_nichos: "dict[str, NichoConfig]",
    nichos_dir: Path | None = None,
) -> "dict[str, NichoConfig]":
    """Load all .yaml niche manifests from the nichos/ directory.

    Args:
        base_nichos: Hardcoded fallback dict from config.py
        nichos_dir: Override directory (defaults to nichos/ next to this file)

    Returns:
        dict of slug -> NichoConfig. Falls back to base_nichos on any error.
    """
    from models.config_models import NichoConfig

    directory = nichos_dir or _NICHOS_DIR

    # Try PyYAML first, fall back to JSON-like loader
    try:
        import yaml as _yaml
    except ImportError:
        logger.warning(
            "⚠️ pyyaml not installed — using hardcoded nichos. "
            "Run: pip install pyyaml"
        )
        return base_nichos

    yaml_files = sorted(directory.glob("*.yaml"))
    # Exclude files starting with _ (like _loader.py companion) and playbooks
    yaml_files = [f for f in yaml_files if not f.stem.startswith("_") and not f.name.endswith(".playbook.yaml")]

    if not yaml_files:
        logger.debug(f"No .yaml niche files found in {directory} — using hardcoded nichos")
        return base_nichos

    loaded: dict[str, NichoConfig] = {}

    for yaml_path in yaml_files:
        try:
            raw = _yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                logger.warning(f"Skipping {yaml_path.name}: expected dict, got {type(raw)}")
                continue

            # Ensure slug is set (fallback to filename stem)
            raw.setdefault("slug", yaml_path.stem)

            # horas comes as YAML list — ensure it's a list of ints
            if "horas" in raw and isinstance(raw["horas"], list):
                raw["horas"] = [int(h) for h in raw["horas"]]

            nicho = NichoConfig(**raw)
            loaded[nicho.slug] = nicho
            logger.debug(f"✅ Loaded niche from YAML: {nicho.slug}")

        except Exception as exc:
            # Surface Pydantic ValidationError details clearly
            from pydantic import ValidationError
            if isinstance(exc, ValidationError):
                logger.error(
                    f"❌ Validation error in {yaml_path.name}:\n"
                    f"{exc}"
                )
                for err in exc.errors():
                    field = " → ".join(str(loc) for loc in err["loc"])
                    logger.error(
                        f"  Field '{field}': {err['msg']} "
                        f"(input: {err.get('input', '?')})"
                    )
            else:
                logger.warning(
                    f"Failed loading {yaml_path.name}: {exc}. "
                    f"Using hardcoded fallback for this niche."
                )
            # Add the hardcoded version if available
            stem = yaml_path.stem
            if stem in base_nichos:
                loaded[stem] = base_nichos[stem]

    if not loaded:
        logger.warning("No valid niche YAMLs loaded — falling back to hardcoded nichos")
        return base_nichos

    # Merge: YAML takes precedence over hardcoded, but keep any hardcoded not in YAML
    merged = {**base_nichos, **loaded}
    logger.info(
        f"📂 Nichos loaded: {len(loaded)}/{len(yaml_files)} from YAML "
        f"(+{len(base_nichos)} hardcoded base)"
    )
    return merged
