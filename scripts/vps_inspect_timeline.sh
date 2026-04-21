#!/usr/bin/env bash
set -euo pipefail
cd "$HOME/video_factory"
source venv/bin/activate

LATEST="$(find . -maxdepth 5 -name 'timeline_*.json' -printf '%T@ %p\n' 2>/dev/null | sort -nr | head -1 | awk '{print $2}')"
echo "timeline: $LATEST"
if [ -z "${LATEST:-}" ] || [ ! -f "$LATEST" ]; then exit 0; fi

python - "$LATEST" <<'PY'
import json, sys
t = json.load(open(sys.argv[1]))
print("scenes:", len(t.get("scenes", [])))
print("captions_present:", bool(t.get("captions")))
ov = t.get("dynamicOverlays", [])
print("dynamicOverlays:", len(ov))
for o in ov:
    print(f"  {o.get('type'):<16} start={o.get('startSeconds'):.2f}s dur={o.get('durationSeconds'):.2f}s "
          f"style={o.get('style','')} pos={o.get('position','')} text={(o.get('text','') or '')[:60]!r}")
PY
