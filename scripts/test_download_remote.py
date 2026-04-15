#!/usr/bin/env python3
from config import settings
from services.http_client import download_file

p = settings.video_cache_dir / "test_debug.mp4"
print("DEST->", p.as_posix())
ok = download_file("https://filesamples.com/samples/video/mp4/sample_640x360.mp4", p, timeout=30)
print("download ok:", ok)
print("exists:", p.exists())
print("size:", p.stat().st_size if p.exists() else None)
