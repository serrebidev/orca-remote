#!/usr/bin/env bash
# Build a .orca-ext archive from the Orca Remote extension source.
#
# Usage:
#   ./build-orca-ext.sh <package-dir> [output.orca-ext]
#
# Example:
#   ./build-orca-ext.sh .

set -euo pipefail

if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
    echo "usage: $0 <package-dir> [output.orca-ext]" >&2
    exit 2
fi

PKG="$1"

if [ ! -d "$PKG" ]; then
    echo "error: $PKG is not a directory" >&2
    exit 1
fi
if [ ! -f "$PKG/manifest.toml" ]; then
    echo "error: $PKG/manifest.toml is missing" >&2
    exit 1
fi

if [ "$#" -eq 2 ]; then
    OUT="$2"
else
    NAME="$(awk -F '"' '/^name *=/ {print $2; exit}' "$PKG/manifest.toml")"
    if [ -z "$NAME" ]; then
        echo "error: could not parse extension name from $PKG/manifest.toml" >&2
        exit 1
    fi
    OUT="$NAME.orca-ext"
fi

OUT_ABS="$(python3 -c 'import os, sys; print(os.path.abspath(sys.argv[1]))' "$OUT")"

python3 - "$PKG" "$OUT_ABS" <<'PY'
from pathlib import Path
import os
import sys
import zipfile

pkg = Path(sys.argv[1]).resolve()
out = Path(sys.argv[2]).resolve()

# This repository also carries docs, tests, and a legacy
# orca-customizations.py installer. Keep those out of the extension
# archive so users get a small runtime-only .orca-ext.
required = [
    "manifest.toml",
    "__init__.py",
    "remote.py",
    "settings_dialog.py",
    "transport.py",
    "protocol.py",
    "keymap.py",
    "braille_table.py",
    "LICENSE",
]

files = [Path(name) for name in required]
vendor = pkg / "vendor"
if vendor.exists():
    for path in sorted(vendor.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(pkg)
        parts = rel.parts
        if "__pycache__" in parts or path.suffix in {".pyc", ".pyo"}:
            continue
        files.append(rel)

missing = [str(path) for path in files if not (pkg / path).is_file()]
if missing:
    for path in missing:
        print(f"error: required extension file missing: {pkg / path}", file=sys.stderr)
    raise SystemExit(1)

out.parent.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as archive:
    for rel in sorted(files, key=lambda p: p.as_posix()):
        source = pkg / rel
        info = zipfile.ZipInfo(rel.as_posix())
        info.date_time = (1980, 1, 1, 0, 0, 0)
        info.external_attr = (0o755 if os.access(source, os.X_OK) else 0o644) << 16
        archive.writestr(info, source.read_bytes(), compress_type=zipfile.ZIP_DEFLATED)

print(f"wrote {out} ({out.stat().st_size} bytes)")
PY
