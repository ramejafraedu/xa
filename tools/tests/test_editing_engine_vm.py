#!/usr/bin/env python3
import json
import time
import traceback
import os
from pathlib import Path

out_dir = Path("tools/tests/tmp_vm_test")
out_dir.mkdir(parents=True, exist_ok=True)

def safe_print(*a, **k):
    print(*a, **k)
    try:
        import sys
        sys.stdout.flush()
    except:
        pass

safe_print("Starting EditingEngine VM smoke test:", time.strftime("%Y-%m-%d %H:%M:%S"))

engine = None
try:
    from tools.editing.editing_engine import EditingEngine, EditingStep, Flow
    safe_print("Imported EditingEngine OK")
    engine = EditingEngine()
    engine.addEditingStep(EditingStep.ADD_CAPTION_SHORT, {"text": "Prueba VM", "set_time_start": 0, "set_time_end": 3})
    schema = engine.dumpEditingSchema()
    safe_print("Schema keys:", list(schema.keys()))
    with open(out_dir / "schema.json", "w", encoding="utf-8") as f:
        json.dump(schema, f, ensure_ascii=False, indent=2)
    safe_print("Schema written to", str(out_dir / "schema.json"))
except Exception as e:
    safe_print("EditingEngine import/usage failed:", str(e))
    safe_print(traceback.format_exc())

# Try rendering image (may fail if deps missing)
if engine is not None:
    try:
        img_out = out_dir / "test_eml_image.png"
        engine.renderImage(str(img_out))
        safe_print("renderImage OK:", str(img_out))
    except Exception as e:
        safe_print("renderImage failed:", str(e))
        safe_print(traceback.format_exc())

    # Try rendering short video (may be slow)
    try:
        vid_out = out_dir / "test_eml_video.mp4"
        engine.renderVideo(str(vid_out))
        safe_print("renderVideo OK:", str(vid_out))
    except Exception as e:
        safe_print("renderVideo failed:", str(e))
        safe_print(traceback.format_exc())

safe_print("Test finished")
