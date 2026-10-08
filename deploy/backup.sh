#!/usr/bin/env bash
# Snapshot the mutable state — one corrupt write to my_rag_stories.json means
# all 10k records. Cron it on the box:
#   17 3 * * * /srv/image-story-pictorial/deploy/backup.sh
# Keeps the newest 5 snapshots of each file in .backups/ (~34 MB total).

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="$ROOT/.backups"
KEEP=5

mkdir -p "$DEST"
ts="$(date +%Y%m%d-%H%M)"
for f in my_rag_stories.json embeddings.f32 embeddings.ids.json; do
  [[ -f "$ROOT/$f" ]] && cp -p "$ROOT/$f" "$DEST/$f.$ts"
done

for f in my_rag_stories.json embeddings.f32 embeddings.ids.json; do
  # delete everything older than the newest KEEP snapshots of this file
  find "$DEST" -maxdepth 1 -name "$f.*" -printf '%T@ %p\n' 2>/dev/null |
    sort -rn | awk -v k="$KEEP" 'NR>k {print $2}' |
    { xargs -r rm -f || true; }
done

echo "backups: $(ls "$DEST" | wc -l) files in $DEST"
