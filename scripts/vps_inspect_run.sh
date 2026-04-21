#!/usr/bin/env bash
set -euo pipefail
cd "$HOME/video_factory"
source venv/bin/activate

LATEST_MANIFEST="$(find workspace/output -maxdepth 2 -name 'job_manifest_*.json' -printf '%T@ %p\n' 2>/dev/null | sort -nr | head -1 | awk '{print $2}')"
echo "manifest: $LATEST_MANIFEST"

python - "$LATEST_MANIFEST" <<'PY'
import json, sys, os
m = json.load(open(sys.argv[1]))
tj = m.get("timeline_json_path") or ""
dj = m.get("director_json_path") or ""
print("timeline_json_path:", tj)
print("director_json_path:", dj)
print("render_backend:", m.get("render_backend"))
print("tts_engine_used:", m.get("tts_engine_used"))

for label, p in [("timeline", tj), ("director", dj)]:
    if p and os.path.exists(p):
        try:
            d = json.load(open(p))
            ov = d.get("dynamicOverlays") or d.get("dynamic_overlays") or []
            print(f"--- {label}: {p} ---")
            print(f"  keys: {sorted(d.keys())[:25]}")
            print(f"  dynamicOverlays: {len(ov)}")
            for o in ov[:20]:
                t = o.get("type") or o.get("overlayType")
                s = o.get("startSeconds", o.get("start", 0))
                dur = o.get("durationSeconds", o.get("duration", 0))
                txt = (o.get("text") or "")[:60]
                print(f"    {t:<16} start={s:.2f}s dur={dur:.2f}s text={txt!r}")
        except Exception as e:
            print(f"  error reading {p}: {e}")
    else:
        print(f"--- {label}: NOT FOUND -> {p}")

# Render checkpoint
ckpt = f"workspace/output/checkpoints/{m['job_id']}/checkpoint_render.json"
if os.path.exists(ckpt):
    try:
        c = json.load(open(ckpt))
        print("--- render checkpoint keys ---", sorted(c.keys())[:40])
        for k in ("render_backend","timeline_payload","director_path","timeline_path","dynamicOverlays"):
            if k in c:
                v = c[k]
                if isinstance(v,(dict,list)):
                    print(f"  {k}: type={type(v).__name__} len={len(v)}")
                else:
                    print(f"  {k}: {v}")
    except Exception as e:
        print("render ckpt err:", e)

# Also check the subtitles .ass: dur of last event
subs = m.get("subs_path","")
if subs and os.path.exists(subs):
    lines = open(subs, encoding='utf-8').read().splitlines()
    evs = [l for l in lines if l.startswith("Dialogue:")]
    print(f"--- subs: {len(evs)} dialogues ---")
    for l in evs[:3]+evs[-3:]:
        print(" ", l[:160])
PY
