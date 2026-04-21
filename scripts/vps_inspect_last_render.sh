#!/usr/bin/env bash
set -euo pipefail
cd "$HOME/video_factory"
source venv/bin/activate

echo "--- latest videos ---"
ls -lht workspace/output/*.mp4 2>/dev/null | head -5 || true

echo "--- latest schema overlays ---"
LATEST_SCHEMA="$(ls -1t workspace/output/schema_*.json 2>/dev/null | head -1 || true)"
if [ -z "${LATEST_SCHEMA:-}" ]; then
    LATEST_SCHEMA="$(ls -1t outputs/schema_*.json 2>/dev/null | head -1 || true)"
fi
if [ -z "${LATEST_SCHEMA:-}" ]; then
    LATEST_SCHEMA="$(find . -maxdepth 4 -name 'schema_*.json' -printf '%T@ %p\n' 2>/dev/null | sort -nr | head -1 | awk '{print $2}')"
fi
echo "schema: $LATEST_SCHEMA"
if [ -n "${LATEST_SCHEMA:-}" ] && [ -f "$LATEST_SCHEMA" ]; then
    python - "$LATEST_SCHEMA" <<'PY'
import json, sys
path = sys.argv[1]
s = json.load(open(path))
tl = s.get("timeline", [])
non_av = [x for x in tl if str(x.get("type","")).lower() not in {"video","audio"}]
print("total_timeline_items:", len(tl))
print("overlays_in_schema:", len(non_av))
for o in non_av[:12]:
    print(f"  {o.get('type'):<16} start={o.get('start_time'):.2f}s dur={o.get('duration'):.2f}s style={o.get('style','')} pos={o.get('position','')}")
print("metadata.overlays_automaticos:", s.get("metadata", {}).get("overlays_automaticos"))
print("metadata.emocion_detectada:", s.get("metadata", {}).get("emocion_detectada"))
PY
fi

echo "--- latest director artifact overlays ---"
LATEST_DIR="$(find . -maxdepth 5 -name 'director_*.json' -not -name 'director_meta_*' -printf '%T@ %p\n' 2>/dev/null | sort -nr | head -1 | awk '{print $2}')"
echo "director: $LATEST_DIR"
if [ -n "${LATEST_DIR:-}" ] && [ -f "$LATEST_DIR" ]; then
    python - "$LATEST_DIR" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
props = d.get("props", {})
ov = props.get("dynamicOverlays", [])
print("dynamicOverlays_in_director:", len(ov))
for o in ov[:12]:
    print(f"  {o.get('type'):<16} start={o.get('startSeconds'):.2f}s dur={o.get('durationSeconds'):.2f}s style={o.get('style','')} pos={o.get('position','')}")
print("audioDurationInSeconds:", props.get("audioDurationInSeconds"))
print("has_captions:", bool(props.get("captions")))
PY
fi
