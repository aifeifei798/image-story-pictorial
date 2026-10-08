#!/usr/bin/env python3
"""Every class token used by the UI must exist in the compiled Tailwind CSS
   (or be one of the hand-written rules in the page <style> block)."""
import pathlib
import re
import sys

STATIC = pathlib.Path("app/static")
css = (STATIC / "tailwind.css").read_text()

# selectors -> plain class names (undo Tailwind's CSS escaping)
def unescape(sel: str) -> str:
    sel = re.sub(r"\\([0-9a-fA-F]{1,6})\s", lambda m: chr(int(m.group(1), 16)), sel)
    return sel.replace("\\", "")

compiled: set[str] = set()
PLACE = "\x00"
for block in re.findall(r"([^{};]+)\{", css):
    block = block.replace("\\,", PLACE).replace("\\2c ", PLACE)
    for raw in block.split(","):
        sel = unescape(raw.strip().replace(PLACE, "\\,"))
        if not sel.startswith("."):
            continue
        sel = sel[1:]
        while True:  # strip trailing pseudo-classes (:hover, ::before, :not(…)…)
            stripped = re.sub(r"::?[a-z-]+(\([^)]*\))?$", "", sel)
            if stripped == sel:
                break
            sel = stripped
        if sel:
            compiled.add(sel)

custom = {"sk", "drift-a", "drift-b", "first-line", "grain"}
tokens: set[str] = set()
for name in ("index.html", "story.html", "app.js"):
    src = (STATIC / name).read_text()
    for chunk in re.findall(r'class(?:Name)?\s*=\s*"([^"]*)"', src):
        tokens.update(t for t in chunk.split() if t)

missing = sorted(t for t in tokens if t not in compiled and t not in custom)
print(f"{len(tokens)} class tokens, {len(compiled)} compiled selectors")
print("MISSING:", missing if missing else "none")
sys.exit(1 if missing else 0)
