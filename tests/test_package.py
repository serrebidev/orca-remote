"""Repository packaging checks for the Orca extension archive."""

from __future__ import annotations

from pathlib import Path
import re
import tomllib


ROOT = Path(__file__).resolve().parent.parent

REQUIRED_EXTENSION_FILES = {
    "manifest.toml",
    "__init__.py",
    "remote.py",
    "settings_dialog.py",
    "transport.py",
    "protocol.py",
    "keymap.py",
    "braille_table.py",
    "LICENSE",
    "vendor/orca_ext_utils/keyboard_grab.py",
}

FORBIDDEN_ARCHIVE_FILES = {
    "README.md",
    "agents.md",
    "install",
    "uninstall",
    "orca-customizations.py",
    "tests/test_protocol.py",
    "docs/architecture.md",
    "orca-scripts/transport.py",
}


def test_manifest_entry_points_at_modern_extension():
    manifest = tomllib.loads((ROOT / "manifest.toml").read_text(encoding="utf-8"))
    assert manifest["extension"]["name"] == "remote"
    assert manifest["extension"]["display-name"] == "Orca Remote"
    assert manifest["extension"]["license"] == "LGPL-2.1-or-later"
    assert manifest["entry"] == {
        "module": "remote",
        "class": "RemoteExtension",
    }


def test_build_script_uses_stdlib_zipfile_not_external_zip():
    script = (ROOT / "build-orca-ext.sh").read_text(encoding="utf-8")
    assert "import zipfile" in script
    assert not re.search(r"\bzip\s+-", script)


def test_build_script_required_files_exist():
    for rel in REQUIRED_EXTENSION_FILES:
        assert (ROOT / rel).is_file(), rel


def test_build_script_excludes_non_runtime_files():
    script = (ROOT / "build-orca-ext.sh").read_text(encoding="utf-8")
    for rel in REQUIRED_EXTENSION_FILES:
        assert f'"{rel}"' in script or f'"{rel.split("/", 1)[0]}"' in script
    for rel in FORBIDDEN_ARCHIVE_FILES:
        assert f'"{rel}"' not in script


def test_line_ending_policy_keeps_scripts_unix_compatible():
    attrs = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert "install text eol=lf" in attrs
    assert "uninstall text eol=lf" in attrs
    assert "*.sh text eol=lf" in attrs
