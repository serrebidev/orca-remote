"""Issue #5: single characters must reach NVDA as spoken names.

When Orca is the host, character navigation and key echo forward one
raw character at a time. NVDA's speech queue treats a lone ``" "`` as
silence (and strips whitespace), and lone punctuation such as ``@``
or ``-`` at NVDA's default symbol level is likewise silent. NVDA's
own character speech expands such characters to their names first;
the host must do the same, localized -- Orca's ``chnames`` table on
the host carries the names in the host's language.
"""

from __future__ import annotations

import ast
from pathlib import Path
from unittest import mock

import pytest

def _protocol():
    import importlib.util
    import sys

    path = Path(__file__).resolve().parent.parent / "protocol.py"
    spec = importlib.util.spec_from_file_location("orca_remote_protocol", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


protocol = _protocol()


@pytest.mark.parametrize("character,name", [
    (" ", "space"),
    ("\t", "tab"),
    ("\n", "newline"),
    (" ", "no break space"),
    ("@", "at"),
    ("-", "dash"),
    ("!", "exclaim"),
    ('"', "quote"),
    ("#", "number"),
    ("&", "and"),
    ("*", "star"),
    (".", "dot"),
    ("/", "slash"),
    ("_", "underline"),
    ("|", "vertical bar"),
    ("~", "tilde"),
])
def test_silent_single_characters_expand_to_their_names(character, name):
    assert protocol.expand_single_character(character) == name


@pytest.mark.parametrize("text", ["a", "A", "1", "á", "hello", "a b", " @", "", "aa"])
def test_anything_else_passes_through_unchanged(text):
    assert protocol.expand_single_character(text) == text


def test_localized_names_win_over_the_english_fallback():
    spanish = {" ": "espacio", "@": "arroba", "-": "guión"}
    assert protocol.expand_single_character(" ", spanish) == "espacio"
    assert protocol.expand_single_character("@", spanish) == "arroba"
    assert protocol.expand_single_character("-", spanish) == "guión"
    # A character the localized table does not name still expands.
    assert protocol.expand_single_character(".", spanish) == "dot"


def test_unknown_symbol_falls_back_to_the_raw_character():
    assert protocol.expand_single_character("🦄") == "🦄"


def test_host_mirror_sends_names_not_raw_characters():
    from tests.test_bypass_chords import _load_remote_module

    mod = _load_remote_module()
    host = mod.RemoteExtension.__new__(mod.RemoteExtension)
    host._settings = {"role": "host"}
    host._transport = object()
    host._loop = object()
    host._mirror_speech = True
    host._last_outbound_speech = ""
    host._last_outbound_speech_at = 0.0
    host._schedule_send = mock.Mock()

    host._on_speech_emitted(" ", "", "")
    host._on_speech_emitted("@", "", "")
    host._on_speech_emitted("word", "", "")

    sent = [c.args[0]["sequence"] for c in host._schedule_send.call_args_list]
    assert sent == [["space"], ["at"], ["word"]]


def test_legacy_expansion_matches():
    source = Path(__file__).resolve().parents[1] / "orca-customizations.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    nodes = [
        node for node in tree.body
        if (isinstance(node, ast.FunctionDef) and node.name == "_expand_single_character")
        or (isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "_CHARACTER_NAMES"
                    for t in node.targets))
    ]
    namespace: dict = {}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), namespace)

    expand = namespace["_expand_single_character"]
    assert expand(" ") == "space"
    assert expand("@") == "at"
    assert expand("-") == "dash"
    assert expand("a") == "a"
    assert expand("hello") == "hello"
