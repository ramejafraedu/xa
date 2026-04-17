"""YAML Config Bridge — V16.1 PRO.

Makes ``config_v16_pro.yaml`` the source of truth for a subset of runtime
knobs, applying its values on top of the already-loaded ``Settings`` object
and the ``NICHOS`` dict. This lets operators tune short-form behavior,
OpenMontage flags, budgets, and per-niche visual styles from a single YAML
without touching ``.env`` or the hardcoded defaults.

Design notes:
- Non-invasive: if the YAML is missing or malformed, the bridge silently
  keeps the original ``Settings``/``NICHOS`` (logged as warnings).
- Additive: only maps keys that actually exist on ``Settings`` /
  ``NichoConfig``. Unknown keys are ignored.
- Idempotent: safe to call multiple times.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger

YAML_FILENAME = "config_v16_pro.yaml"


def _find_yaml_path(base_dir: Path) -> Path | None:
    candidate = base_dir / YAML_FILENAME
    if candidate.exists():
        return candidate
    return None


def _load_yaml(path: Path) -> dict[str, Any] | None:
    try:
        import yaml as _yaml
    except Exception as exc:  # pragma: no cover - yaml is in requirements
        logger.warning(f"[yaml_bridge] pyyaml unavailable: {exc}")
        return None
    try:
        raw = _yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning(f"[yaml_bridge] Failed reading {path.name}: {exc}")
        return None
    if not isinstance(raw, dict):
        logger.warning(f"[yaml_bridge] {path.name} is not a YAML mapping")
        return None
    return raw


def _safe_setattr(settings_obj: Any, name: str, value: Any) -> bool:
    if not hasattr(settings_obj, name):
        return False
    try:
        setattr(settings_obj, name, value)
        return True
    except Exception as exc:
        logger.debug(f"[yaml_bridge] setattr({name}) rejected: {exc}")
        return False


def apply_yaml_overrides(settings_obj: Any, nichos: dict) -> dict[str, Any]:
    """Apply ``config_v16_pro.yaml`` overrides in-place.

    Returns a summary dict with what was applied (for diagnostics / tests).
    """
    base_dir = getattr(settings_obj, "base_dir", None)
    if base_dir is None:
        base_dir = Path(__file__).resolve().parent.parent
    yaml_path = _find_yaml_path(Path(base_dir))
    if yaml_path is None:
        logger.debug("[yaml_bridge] No config_v16_pro.yaml found, skipping overrides")
        return {"applied": False, "reason": "file_missing"}

    raw = _load_yaml(yaml_path)
    if raw is None:
        return {"applied": False, "reason": "parse_error"}

    applied: dict[str, Any] = {"applied": True, "keys": [], "nichos_styles": {}}

    video = raw.get("video") or {}
    if isinstance(video, dict):
        mapping = {
            "min_duration": "min_video_duration",
            "max_duration": "max_video_duration",
            "target_duration": "target_duration_seconds",
        }
        for yaml_key, settings_key in mapping.items():
            if yaml_key in video and _safe_setattr(settings_obj, settings_key, int(video[yaml_key])):
                applied["keys"].append(settings_key)

    features = raw.get("features") or {}
    if isinstance(features, dict):
        flag_mapping = {
            "openmontage_tools": "enable_openmontage_free_tools",
            "openmontage_skills": "openmontage_enable_analysis",
            "cost_tracking": "enable_cost_governance",
        }
        for yaml_key, settings_key in flag_mapping.items():
            if yaml_key in features and _safe_setattr(settings_obj, settings_key, bool(features[yaml_key])):
                applied["keys"].append(settings_key)

    cost = raw.get("cost_tracking") or {}
    if isinstance(cost, dict):
        if "budget_total_usd" in cost and _safe_setattr(
            settings_obj, "daily_budget_usd", float(cost["budget_total_usd"])
        ):
            applied["keys"].append("daily_budget_usd")
        if "single_action_approval_usd" in cost and _safe_setattr(
            settings_obj, "single_action_approval_usd", float(cost["single_action_approval_usd"])
        ):
            applied["keys"].append("single_action_approval_usd")

    themes = raw.get("themes") or {}
    if isinstance(themes, dict) and "default" in themes:
        if _safe_setattr(settings_obj, "default_theme", str(themes["default"])):
            applied["keys"].append("default_theme")

    short_form = raw.get("short_form") or {}
    if isinstance(short_form, dict):
        short_mapping = {
            "max_scenes": "short_max_scenes",
            "min_scenes": "short_min_scenes",
            "scene_min_seconds": "short_scene_min_seconds",
            "scene_max_seconds": "short_scene_max_seconds",
            "hook_max_seconds": "short_hook_max_seconds",
            "transition_seconds": "short_transition_seconds",
            "script_word_min": "short_script_word_min",
            "script_word_max": "short_script_word_max",
            "enforce_hard_limit": "enforce_duration_hard_limit",
            "enforce_micro_loop_ending": "enforce_micro_loop_ending",
        }
        for yaml_key, settings_key in short_mapping.items():
            if yaml_key in short_form:
                value = short_form[yaml_key]
                if isinstance(value, bool):
                    coerced: Any = value
                elif settings_key in {"short_max_scenes", "short_min_scenes",
                                      "short_script_word_min", "short_script_word_max"}:
                    coerced = int(value)
                else:
                    coerced = float(value)
                if _safe_setattr(settings_obj, settings_key, coerced):
                    applied["keys"].append(settings_key)

    nichos_yaml = raw.get("nichos") or []
    if isinstance(nichos_yaml, list):
        for entry in nichos_yaml:
            if not isinstance(entry, dict):
                continue
            slug = str(entry.get("slug") or "").strip()
            style = str(entry.get("style") or "").strip()
            if not slug or not style:
                continue
            nicho = nichos.get(slug)
            if nicho is None:
                continue
            try:
                if hasattr(nicho, "model_copy"):
                    updated = nicho.model_copy(update={"style_playbook": style})
                    nichos[slug] = updated
                else:
                    setattr(nicho, "style_playbook", style)
                applied["nichos_styles"][slug] = style
            except Exception as exc:
                logger.debug(f"[yaml_bridge] could not set style on nicho '{slug}': {exc}")

    logger.info(
        f"[yaml_bridge] Applied {len(applied['keys'])} setting overrides and "
        f"{len(applied['nichos_styles'])} niche styles from {yaml_path.name}"
    )
    return applied
