"""Orca Remote's shortcuts must not collide with Orca's built-in ones.

Orca refuses an extension keybinding that is already used by one of
its own commands ("COMMAND MANAGER: Refusing keybinding ..."), and the
command then has no shortcut at all. Orca+Ctrl+M (flat review "end of
line" in the laptop layout) silently disabled mute this way. This test
reads the installed Orca's command definitions and fails on any
overlap, in either layout. It is skipped where Orca is not installed.
"""

from __future__ import annotations

import ast
import importlib.machinery
from pathlib import Path

import pytest

_REMOTE = Path(__file__).resolve().parent.parent / "remote.py"

# Local names remote.py binds its masks to, as modifier sets.
_REMOTE_MASKS = {
    "ctrl": frozenset({"ORCA", "CTRL"}),
    "alt": frozenset({"ORCA", "ALT"}),
    "ctrl_shift": frozenset({"ORCA", "CTRL", "SHIFT"}),
}
_TOKENS = ("ORCA", "CTRL", "ALT", "SHIFT")


def _orca_dir() -> Path | None:
    # PathFinder looks at sys.path directly: other tests replace the
    # "orca" entry in sys.modules with a stub that has no __spec__.
    spec = importlib.machinery.PathFinder.find_spec("orca")
    if spec is None or not spec.submodule_search_locations:
        return None
    path = Path(next(iter(spec.submodule_search_locations)))
    return path if (path / "keybindings.py").is_file() else None


def _mask_tokens(node: ast.AST) -> frozenset[str]:
    tokens: set[str] = set()
    for sub in ast.walk(node):
        name = getattr(sub, "attr", None) or getattr(sub, "id", None)
        if isinstance(name, str) and name.endswith("_MASK"):
            tokens.update(t for t in _TOKENS if t in name.split("_"))
    return frozenset(tokens)


def _is_keybinding_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    name = getattr(func, "attr", None) or getattr(func, "id", None)
    return name == "KeyBinding" and bool(node.args) and isinstance(node.args[0], ast.Constant)


def _orca_bindings(orca_dir: Path) -> set[tuple[str, frozenset[str]]]:
    found: set[tuple[str, frozenset[str]]] = set()
    for path in orca_dir.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if _is_keybinding_call(node) and len(node.args) > 1:
                found.add((str(node.args[0].value).lower(), _mask_tokens(node.args[1])))
    return found


def _remote_bindings() -> set[tuple[str, frozenset[str]]]:
    tree = ast.parse(_REMOTE.read_text(encoding="utf-8"))
    found: set[tuple[str, frozenset[str]]] = set()
    for node in ast.walk(tree):
        if _is_keybinding_call(node) and len(node.args) > 1:
            mask = node.args[1]
            if isinstance(mask, ast.Name) and mask.id in _REMOTE_MASKS:
                found.add((str(node.args[0].value).lower(), _REMOTE_MASKS[mask.id]))
    return found


def test_remote_bindings_are_parsed():
    # Guard the guard: if remote.py's mask names change, this test
    # must notice instead of silently comparing an empty set.
    assert len(_remote_bindings()) >= 6


def test_no_shortcut_collides_with_installed_orca():
    orca_dir = _orca_dir()
    if orca_dir is None:
        pytest.skip("Orca is not installed here")
    clashes = sorted(
        f"Orca+{'+'.join(sorted(mods - {'ORCA'}))}+{key}"
        for key, mods in _remote_bindings() & _orca_bindings(orca_dir)
    )
    assert not clashes, f"Orca already uses: {', '.join(clashes)}"
