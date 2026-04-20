"""Quick verifier for V16 PRO short-form settings on remote deploy."""
from __future__ import annotations

from config import settings
from pipeline.content_gen import _script_profile as cg_profile
from agents.script_agent import _script_profile as sa_profile


def main() -> None:
    print("=" * 50)
    print("V16 PRO SHORT-FORM SETTINGS CHECK")
    print("=" * 50)
    print(f"target_duration_seconds    = {settings.target_duration_seconds}")
    print(f"max_video_duration         = {settings.max_video_duration}")
    print(f"min_video_duration         = {settings.min_video_duration}")
    print(f"enforce_duration_hard_limit= {settings.enforce_duration_hard_limit}")
    print(f"auto_trim_if_over          = {settings.auto_trim_if_over}")
    print(f"short_script_word_min/max  = {settings.short_script_word_min} - {settings.short_script_word_max}")
    print(f"short_max_scenes           = {settings.short_max_scenes}")
    print(f"short_min_scenes           = {settings.short_min_scenes}")
    print(f"short_scene_min/max secs   = {settings.short_scene_min_seconds} - {settings.short_scene_max_seconds}")
    print(f"short_hook_max_seconds     = {settings.short_hook_max_seconds}")
    print(f"short_transition_seconds   = {settings.short_transition_seconds}")
    print(f"enforce_micro_loop_ending  = {settings.enforce_micro_loop_ending}")
    print("-" * 50)
    for platform in ("shorts", "tiktok", "reels", "facebook"):
        print(f"content_gen._script_profile({platform!r}) -> {cg_profile(platform)}")
        print(f"script_agent._script_profile({platform!r}) -> {sa_profile(platform)}")
    print("=" * 50)
    print("OK")


if __name__ == "__main__":
    main()
