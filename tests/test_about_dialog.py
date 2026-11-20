"""About dialog: every link is a focus stop; credits points at CREDITS.md."""
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _stub_gtk():
    created = []
    for name in ("gi", "gi.repository", "gi.repository.Gtk"):
        if name not in sys.modules:
            sys.modules[name] = types.ModuleType(name)
            created.append(name)
    sys.modules["gi"].require_version = lambda *a, **kw: None
    sys.modules["gi"].repository = sys.modules["gi.repository"]
    sys.modules["gi.repository"].Gtk = sys.modules["gi.repository.Gtk"]
    return created


_created_stubs = _stub_gtk()
import settings_dialog  # noqa: E402

# Drop the stubs again: a bare ModuleType has __spec__ = None, and
# leaving one in sys.modules makes importlib.util.find_spec("gi")
# raise ValueError in every test module collected after this one
# (test_portal_thread died at collection on GitHub runners this way).
# settings_dialog keeps its own Gtk reference, so it is unaffected.
for _name in _created_stubs:
    sys.modules.pop(_name, None)


def test_gtk_stubs_do_not_leak_into_sys_modules():
    # Regression: the spec-less gi stub this module installs for its
    # import used to stay in sys.modules and kill collection of
    # test_portal_thread (ValueError: gi.__spec__ is None) on runners
    # without a real gi.
    import importlib.util

    gi = sys.modules.get("gi")
    assert gi is None or gi.__spec__ is not None
    importlib.util.find_spec("gi")  # must not raise


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
