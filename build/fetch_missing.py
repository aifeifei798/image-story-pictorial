#!/usr/bin/env python3
"""Download the images the dataset was created without.

3 records carry a remote URL in ``local_image_path`` and no local .webp, so
`/api/images/{id}` answers `307` to that host forever. This fetches them once
so the site is self-contained (`/api/health`'s ``missing_local_images`` drops
to 0).

Usage (repo root, only when network access to the image host is allowed):
    .venv/bin/python build/fetch_missing.py [--limit 10] [--dry-run]

Records whose download fails are left alone — the 307 fallback keeps working.
Nothing else in the dataset is touched, so no restart of the vector build is
needed afterwards; just `./serve.sh restart` so the new files are picked up.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
DATA = BASE / "my_rag_stories.json"
IMAGE_DIR = BASE / "downloaded_images"
TIMEOUT = 30


def main() -> None:
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else 10
    dry = "--dry-run" in sys.argv

    records = json.loads(DATA.read_text(encoding="utf-8"))
    # A record needs a fetch only when it has no local file AND a remote URL.
    todo = []
    for r in records:
        sid = r["id"]
        if (IMAGE_DIR / f"{sid}.webp").is_file():
            continue
        url = r.get("image_url")
        if url and url.startswith("https://"):
            todo.append((sid, url))
    todo = todo[:limit]
    print(f"{len(todo)} records lack a local image (limit {limit})")
    if not todo:
        return

    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    ok = 0
    for sid, url in todo:
        dest = IMAGE_DIR / f"{sid}.webp"
        if dry:
            print(f"  would fetch {url} -> {dest.name}")
            continue
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "image-story-pictorial/1.0"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                blob = resp.read()
            if len(blob) < 30 or blob[0:4] != b"RIFF" or blob[8:12] != b"WEBP":
                raise ValueError("not a webp payload")
            dest.write_bytes(blob)
            ok += 1
            print(f"  fetched {sid} ({len(blob) / 1024:.0f} KB) -> {dest.name}")
        except (urllib.error.URLError, OSError, ValueError) as exc:
            print(f"  {sid}: {exc} — left to the 307 fallback")
    print(f"done: {ok}/{len(todo)} saved; run ./serve.sh restart")


if __name__ == "__main__":
    main()
