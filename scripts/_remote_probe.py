"""Remote probe for V16.1 PRO deployment + Gemini-everywhere health check."""
from config import settings, NICHOS

print("=== V16.1 PRO Remote Probe ===")
print(f"target_duration_seconds     = {settings.target_duration_seconds}")
print(f"max_video_duration          = {settings.max_video_duration}")
print(f"min_video_duration          = {settings.min_video_duration}")
print(f"short_max_scenes            = {settings.short_max_scenes}")
print(f"enforce_duration_hard_limit = {settings.enforce_duration_hard_limit}")
print(f"gemini_everywhere_mode      = {settings.gemini_everywhere_mode}")
print(f"gemini_visual_boost_prompt  = {bool(settings.gemini_visual_boost_prompt)}")
print(f"gemini_control_plane_enabled= {settings.gemini_control_plane_enabled}")
print(f"gemini_control_plane_quality= {settings.gemini_control_plane_quality_default}")
print(f"gemini keys                 = {len(settings.get_gemini_keys())}")
print(f"pexels keys                 = {len(settings.pexels_keys)}")
print(f"elevenlabs_ok               = {bool(settings.elevenlabs_api_key)}")
print(f"use_vertex_ai               = {settings.use_vertex_ai}")
print(f"workspace                   = {settings.workspace}")

print("--- nichos with style_playbook ---")
for slug, n in NICHOS.items():
    sp = getattr(n, "style_playbook", None)
    if sp:
        print(f"  {slug:30s} -> {sp}")

print("--- imports ---")
from pipeline import content_gen, image_gen, video_stock, renderer_remotion  # noqa
from lib import yaml_config_bridge, checkpoint_integration  # noqa
from state_manager import AssetHistory, get_asset_history  # noqa
from services.gemini_visual_enhancer import (
    enhance_scene_prompts,
    enhanced_keywords,
)  # noqa
print("all pipeline imports OK")
print(f"asset_history path          = {get_asset_history().path}")

print("--- gemini visual enhancer live test ---")
sample_scenes = [
    "En 1962 la CIA ejecuto un experimento con LSD en Nueva York.",
    "Los archivos desclasificados revelan ordenes firmadas por altos oficiales.",
    "Ningun agente fue procesado por los danos en civiles.",
]
try:
    out = enhance_scene_prompts(
        sample_scenes,
        niche_visual="dark documentary, archival footage, low-key lighting",
        style_playbook="minimalist-diagram",
        tone="misterioso y narrativo",
    )
    print(f"enhanced {len(out)} scenes")
    for i, entry in enumerate(out):
        vp = (entry.get("visual_prompt") or "")[:140]
        kws = entry.get("stock_keywords", [])
        print(f"  [{i+1}] mood={entry.get('mood'):<14s} kw={','.join(kws[:4])}")
        print(f"      vp: {vp}")
except Exception as exc:
    print(f"enhancer error: {exc}")

print("--- OK ---")
