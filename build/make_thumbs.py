#!/usr/bin/env python3
"""Downscale every local .webp into downloaded_thumbs/ for the gallery grid.

The grid paints 1:2 cards, so a 480px-wide thumbnail costs ~25 KB against the
~146 KB original — a 25-card page drops from ~3.6 MB to ~0.4 MB. The detail
page, the lightbox and the slideshow keep serving the original.

Usage:
    # 1. start from the repo root
    .venv/bin/python build/make_thumbs.py [--width 480] [--quality 72] [--jobs 4]

Output (repo root):
    downloaded_thumbs/{id}.webp   same stems as downloaded_images/{id}.webp

Needs an external encoder: ImageMagick `convert` (preferred) or `cwebp`.
Without either, the script reports and exits 0 — the API falls back to the
original file (`GET /api/images/{id}?thumb=1`), so the site still works.
Re-running is cheap: a thumbnail that is already as new as its source is
skipped. Remember to `./serve.sh restart` afterwards (nothing is cached).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
SRC = BASE / "downloaded_images"
DST = BASE / "downloaded_thumbs"


def _encoder() -> tuple[list[str], str] | None:
    """(command prefix, kind) — ``kind`` only spells the resize argument."""
    for exe in ("magick", "convert", "cwebp"):
        found = shutil.which(exe)
        if found:
            return [found], "cwebp" if exe == "cwebp" else "convert"
    return None


def _cmd(enc: list[str], width: int, quality: int, src: Path, dst: Path, kind: str) -> list[str]:
    if kind == "cwebp":
        return enc + ["-resize", str(width), "0", "-q", str(quality), "-quiet", str(src), "-o", str(dst)]
    return enc + [str(src), "-resize", f"{width}x", "-quality", str(quality), "-strip", f"webp:{dst}"]


def _make(args: list[str]) -> bool:
    try:
        subprocess.run(args, check=True, capture_output=True, timeout=120)
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


def main() -> None:
    width = int(sys.argv[sys.argv.index("--width") + 1]) if "--width" in sys.argv else 480
    quality = int(sys.argv[sys.argv.index("--quality") + 1]) if "--quality" in sys.argv else 72
    jobs = int(sys.argv[sys.argv.index("--jobs") + 1]) if "--jobs" in sys.argv else 4

    enc = _encoder()
    if not enc:
        print("no encoder found (need ImageMagick `convert` or `cwebp`) — nothing to do; "
              "the API serves originals through /api/images/{id}?thumb=1")
        return
    prefix, kind = enc

    if not SRC.is_dir():
        sys.exit(f"missing source dir: {SRC}")
    DST.mkdir(parents=True, exist_ok=True)

    todo: list[tuple[Path, Path]] = []
    for src in sorted(SRC.glob("*.webp")):
        dst = DST / src.name
        if dst.is_file() and dst.stat().st_mtime >= src.stat().st_mtime:
            continue  # already as new as its source
        todo.append((src, dst))

    total = len(list(SRC.glob("*.webp")))
    print(f"{total} source images, {len(todo)} to build ({kind}, {width}px, q{quality}, jobs={jobs})")
    if not todo:
        print("nothing to do")
        return

    started = time.time()
    done = ok = 0
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        for good in pool.map(
            lambda pair: _make(_cmd(prefix, width, quality, pair[0], pair[1], kind)), todo
        ):
            done += 1
            ok += 1 if good else 0
            if done % 100 == 0 or done == len(todo):
                rate = done / max(1e-9, time.time() - started)
                print(f"  {done}/{len(todo)}  ok={ok}  {rate:.0f}/s", flush=True)

    size = sum(f.stat().st_size for f in DST.glob("*.webp"))
    print(f"done: {DST} ({size / 1e6:.0f} MB, avg {size / max(1, len(list(DST.glob('*.webp')))) / 1e3:.0f} KB)"
          f" — restart the site to pick them up")


if __name__ == "__main__":
    main()
