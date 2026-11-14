"""Repository packaging checks for the Orca extension archive."""

from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sys
from pathlib import Path
import re
import tomllib
import zipfile

import pytest


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
    "inject.py",
    "LICENSE",
    "vendor/orca_ext_utils/keyboard_grab.py",
}

FORBIDDEN_ARCHIVE_FILES = {
    "README.md",
    "agents.md",
    "install",
    "install-extension",
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
    assert "install-extension text eol=lf" in attrs
    assert "*.sh text eol=lf" in attrs


def _init_class_constants() -> dict[str, str]:
    """Literal string constants of the Extension subclass in __init__.py."""

    tree = ast.parse((ROOT / "__init__.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        bases = {getattr(base, "id", None) or getattr(base, "attr", None) for base in node.bases}
        if "Extension" not in bases:
            continue
        constants: dict[str, str] = {}
        for child in node.body:
            if isinstance(child, ast.Assign) and isinstance(child.value, ast.Constant):
                for target in child.targets:
                    if isinstance(target, ast.Name):
                        constants[target.id] = child.value.value
        return constants
    pytest.fail("__init__.py must define a class deriving from Extension")


def test_init_declares_metadata_for_released_orca():
    """Released Orca reads extension metadata by parsing __init__.py.

    Its loader looks for a ClassDef whose base is named Extension plus
    literal GROUP_LABEL / DESCRIPTION / VERSION / AUTHOR / WEBSITE
    assignments. With nothing to find it lists the package as
    "Invalid" and never loads it -- so this file must stay a real
    class, not a plain re-export.
    """

    constants = _init_class_constants()
    for name in ("GROUP_LABEL", "DESCRIPTION", "VERSION", "AUTHOR", "WEBSITE"):
        assert constants.get(name), f"__init__.py is missing a literal {name}"


def test_init_version_matches_manifest():
    manifest = tomllib.loads((ROOT / "manifest.toml").read_text(encoding="utf-8"))
    assert _init_class_constants()["VERSION"] == manifest["extension"]["version"]


def test_install_extension_script_unpacks_and_approves(tmp_path):
    """The unpack path released Orca needs: extract + approve."""

    if sys.platform == "win32" or shutil.which("bash") is None or shutil.which("python3") is None:
        pytest.skip("the install script targets Linux machines with bash and python3")

    archive = tmp_path / "remote.orca-ext"
    build = subprocess.run(
        ["bash", (ROOT / "build-orca-ext.sh").as_posix(), ROOT.as_posix(),
         archive.as_posix()],
        capture_output=True, text=True, check=False,
    )
    assert build.returncode == 0, build.stderr

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "orca-calls.log"
    orca = fake_bin / "orca"
    orca.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "--help" ]; then echo "  --approve-extension FILE"; exit 0; fi\n'
        'echo "$@" >> "$ORCA_LOG"\n',
        encoding="utf-8",
    )
    orca.chmod(0o755)

    data_home = tmp_path / "data"
    env = dict(os.environ)
    env.update({
        "PATH": f"{fake_bin}{os.pathsep}{env.get('PATH', '')}",
        "XDG_DATA_HOME": str(data_home),
        "ORCA_LOG": str(log),
    })
    install = subprocess.run(
        ["bash", (ROOT / "install-extension").as_posix(), archive.as_posix()],
        capture_output=True, text=True, env=env, check=False,
    )
    assert install.returncode == 0, install.stderr

    installed = data_home / "orca" / "extensions" / "remote"
    assert (installed / "manifest.toml").is_file()
    assert (installed / "__init__.py").is_file()
    assert (installed / "remote.py").is_file()
    assert log.read_text(encoding="utf-8").strip() == "--approve-extension remote"
    assert "orca --replace" in install.stdout


def _load_legacy_installer():
    import importlib.machinery
    import importlib.util

    # The installer has no .py suffix, so it needs an explicit loader.
    loader = importlib.machinery.SourceFileLoader(
        "orca_remote_install", str(ROOT / "install"),
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


@pytest.mark.parametrize("name", ["../victim", "absolute", "other-extension"])
def test_installer_rejects_manifest_names_outside_remote(tmp_path, name):
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "manifest.toml").write_text("keep me", encoding="utf-8")
    if name == "absolute":
        name = victim.as_posix()
    archive = tmp_path / "bad.orca-ext"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("manifest.toml", '[extension]\nname = ' + repr(name) + '\n')
    script = (ROOT / "install-extension").read_text(encoding="utf-8")
    unpacker = script.split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]

    result = subprocess.run(
        [sys.executable, "-c", unpacker, str(archive), str(tmp_path / "extensions")],
        capture_output=True, text=True,
    )

    assert result.returncode != 0
    assert "extension name must be 'remote'" in result.stderr
    assert (victim / "manifest.toml").read_text(encoding="utf-8") == "keep me"


def test_legacy_installer_enables_auto_connect(tmp_path, monkeypatch):
    """The install argument rewrite must not defeat its own check.

    The installer rewrites the "host"/"key" sentinels, including the
    line that used to compare against them, so auto-connect silently
    stayed off. It now flips ORCA_REMOTE_AUTO_CONNECT instead.
    """

    installer = _load_legacy_installer()
    monkeypatch.chdir(ROOT)
    dest = tmp_path / "orca-customizations.py"

    installer.make_orca_customizations(str(dest), "127.0.0.1", "6837", "test4120")

    text = dest.read_text(encoding="utf-8")
    assert 'YOUR_NVDAREMOTE_SERVER_ADDRESS = "127.0.0.1"' in text
    assert 'YOUR_NVDAREMOTE_KEY = "test4120"' in text
    assert "ORCA_REMOTE_AUTO_CONNECT = True" in text
    # The key-forwarding frame type must survive the rewrite: replacing
    # every "key" literal turned type="key" into the channel name, so
    # forwarding sent frames peers ignored.
    assert 'type="key"' in text
    assert 'type="test4120"' not in text


def test_legacy_installer_without_args_keeps_auto_connect_off(tmp_path, monkeypatch):
    installer = _load_legacy_installer()
    monkeypatch.chdir(ROOT)
    dest = tmp_path / "orca-customizations.py"

    installer.make_orca_customizations(str(dest))

    text = dest.read_text(encoding="utf-8")
    assert "ORCA_REMOTE_AUTO_CONNECT = False" in text
    assert 'YOUR_NVDAREMOTE_SERVER_ADDRESS = "host"' in text


def test_legacy_installer_preserves_backslashes_and_newlines_in_settings(tmp_path, monkeypatch):
    installer = _load_legacy_installer()
    monkeypatch.chdir(ROOT)
    dest = tmp_path / "orca-customizations.py"
    secret = "channel\\1\nwith\\backslashes"

    installer.make_orca_customizations(str(dest), "localhost", "6837", secret)

    tree = ast.parse(dest.read_text(encoding="utf-8"))
    assignments = {
        node.targets[0].id: ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
        and isinstance(node.targets[0], ast.Name)
    }
    assert assignments["YOUR_NVDAREMOTE_KEY"] == secret


def test_legacy_install_then_uninstall_fresh_account_from_any_cwd(tmp_path):
    """No Orca data dir yet, run from elsewhere, XDG_DATA_HOME honoured."""

    data = tmp_path / "data"
    env = {**os.environ, "HOME": str(tmp_path / "home"), "XDG_DATA_HOME": str(data)}
    orca = data / "orca"

    install = subprocess.run(
        [sys.executable, str(ROOT / "install"), "127.0.0.1", "6837", "k"],
        input="\n", capture_output=True, text=True, cwd=tmp_path, env=env,
    )
    assert install.returncode == 0, install.stderr
    assert "--install-extension" not in install.stdout
    custom = (orca / "orca-customizations.py").read_text(encoding="utf-8")
    assert "ORCA_REMOTE_AUTO_CONNECT = True" in custom
    assert (orca / "orca-scripts" / "transport.py").is_file()

    uninstall = subprocess.run(
        [sys.executable, str(ROOT / "uninstall")],
        capture_output=True, text=True, cwd=tmp_path, env=env,
    )
    assert uninstall.returncode == 0, uninstall.stderr
    assert not (orca / "orca-customizations.py").exists()
    assert not list((orca / "orca-scripts").glob("*.py"))
