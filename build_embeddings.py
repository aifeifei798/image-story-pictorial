#!/usr/bin/env python3
"""Embed every record via llama-server and write embeddings.f32 + ids.json.

Usage:
    # embedding 服务先起好 (默认 http://localhost:8023)
    ./llama-server.sh  # 在 granite-embedding-97m-multilingual-r2/ 里
    EMBED_URL=http://localhost:8023 .venv/bin/python build_embeddings.py [--batch 32]

Output (repo root):
    embeddings.f32       raw little-endian float32, (N, 384) row-major
    embeddings.ids.json  story ids in the same row order as STORIES

~10k records take about a minute (batch 32 ≈ 0.1 s/req on RTX 5090).
Re-run after my_rag_stories.json changes; the API refuses to serve
a stale matrix whose row count != dataset size.
"""

from __future__ import annotations

import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import embeddings as E  # noqa: E402
from app.dataset import STORIES  # noqa: E402

BATCH = int(sys.argv[sys.argv.index("--batch") + 1]) if "--batch" in sys.argv else 32


def main() -> None:
    if not E.ping():
        sys.exit(f"embedding 服务不可达: {E.EMBED_URL} — 先启动 llama-server.sh")
    texts = [E.record_text(r) for r in STORIES]
    n = len(texts)
    print(f"{n} records -> {E.EMBED_FILE} (batch={BATCH}, url={E.EMBED_URL})")
    # Write to .part files, rename only on success — a killed run leaves the
    # previous complete matrix on disk instead of a short-row corpse.
    tmp_vecs, tmp_ids = f"{E.EMBED_FILE}.part", f"{E.EMBED_IDS}.part"
    with open(tmp_vecs, "wb") as fh:
        done = 0
        for i in range(0, n, BATCH):
            chunk = texts[i : i + BATCH]
            vecs = E.embed_inputs(chunk)
            assert len(vecs) == len(chunk), f"batch {i}: got {len(vecs)} vecs"
            for v in vecs:
                assert len(v) == E.DIM, f"dim {len(v)} != {E.DIM}"
                fh.write(struct.pack(f"<{E.DIM}f", *v))
            done += len(chunk)
            print(f"  {done}/{n}", flush=True)
    with open(tmp_ids, "w", encoding="utf-8") as fh:
        import json

        json.dump([r["id"] for r in STORIES], fh)
    os.replace(tmp_vecs, E.EMBED_FILE)
    os.replace(tmp_ids, E.EMBED_IDS)
    size = os.path.getsize(E.EMBED_FILE)
    print(f"done: {E.EMBED_FILE} ({size / 1e6:.1f} MB), {E.EMBED_IDS}")


if __name__ == "__main__":
    main()
