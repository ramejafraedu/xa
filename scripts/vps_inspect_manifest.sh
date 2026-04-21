#!/usr/bin/env bash
set -euo pipefail
cd "$HOME/video_factory"
source venv/bin/activate

LATEST_MANIFEST="$(find workspace/output -maxdepth 2 -name 'job_manifest_*.json' -printf '%T@ %p\n' 2>/dev/null | sort -nr | head -1 | awk '{print $2}')"
echo "manifest: $LATEST_MANIFEST"

# Also find the associated checkpoint folder and artifacts for the same timestamp
TS="$(basename "$LATEST_MANIFEST" | sed -E 's/.*_([0-9]+)\.json/\1/')"
echo "timestamp: $TS"

python - "$LATEST_MANIFEST" "$TS" <<'PY'
import json, sys, glob, os
mpath, ts = sys.argv[1], sys.argv[2]
m = json.load(open(mpath))
print("--- manifest keys ---")
print(sorted(m.keys()))
print("--- manifest summary ---")
for k in ["job_id","video_path","render_profile","pipeline_type","subs_path",
         "duration_seconds","overlays_count","pipeline_version","render_engine",
         "subtitle_engine","audio_path"]:
    if k in m:
        print(f"{k}: {m[k]}")

# look for artifacts with the timestamp
print("--- artifacts ---")
for pat in [
    f"workspace/temp/timeline_*{ts}*.json",
    f"workspace/temp/director_*{ts}*.json",
    f"workspace/artifacts/**/*{ts}*.json",
    f"workspace/output/checkpoints/**/*{ts}*/**",
]:
    matches = glob.glob(pat, recursive=True)[:5]
    for p in matches:
        print(" ", p)

# check checkpoint dir for the run
ckpt_root = f"workspace/output/checkpoints"
for d in os.listdir(ckpt_root):
    if ts in d:
        full = os.path.join(ckpt_root, d)
        print("--- checkpoint dir:", full, "---")
        for f in sorted(os.listdir(full)):
            print(" ", f)
PY
