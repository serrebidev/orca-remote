"""About dialog: every link is a focus stop; credits points at CREDITS.md."""
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _stub_gtk():
    for name in ("gi", "gi.repository", "gi.repository.Gtk"):
        if name not in sys.modules:
            sys.modules[name] = types.ModuleType(name)
    sys.modules["gi"].require_version = lambda *a, **kw: None
    sys.modules["gi"].repository = sys.modules["gi.repository"]
    sys.modules["gi.repository"].Gtk = sys.modules["gi.repository.Gtk"]


_stub_gtk()
import settings_dialog  # noqa: E402


def test_support_links_list_every_project_link():
    labels = [label for label, _ in settings_dialog.SUPPORT_LINKS]
    assert labels == [
        "GitHub: serrebidev/orca-remote",
        "Forgejo: serrebi/orca-remote",
        "Projects: projects.serrebiradio.com",
        "Telegram: SerrebiProjects",
        "Full credits (CREDITS.md)",
    ]
    assert settings_dialog.SUPPORT_LINKS[-1][1] == (
        "https://git.serrebiradio.com/serrebi/orca-remote/src/branch/main/CREDITS.md"
    )
