"""Exercise the portal worker against a real GLib context in isolation."""

from pathlib import Path
import importlib.util
import subprocess
import sys

import pytest


def _gi_available() -> bool:
    """True only when a real gi can be found for this interpreter.

    find_spec raises ValueError when another test module has parked a
    bare stub named "gi" in sys.modules (its __spec__ is None); that
    means no usable gi here, not a collection error.
    """

    try:
        return importlib.util.find_spec("gi") is not None
    except (ImportError, ValueError):
        return False


@pytest.mark.skipif(sys.platform == "win32", reason="requires Linux GLib bindings")
@pytest.mark.skipif(
    not _gi_available(),
    reason="requires gi (PyGObject) bindings, absent on this interpreter",
)
def test_portal_wait_does_not_dispatch_orca_main_context():
    script = r'''
import importlib.util
import sys
import threading
from types import SimpleNamespace
from gi.repository import GLib

spec = importlib.util.spec_from_file_location("inject", sys.argv[1])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
mod._PORTAL_CONSENT_TIMEOUT_SECONDS = 1
portal = mod._PortalKeyboard(print)

class Bus:
    def get_unique_name(self):
        return ":1.2"

    def signal_subscribe(self, *args):
        self.callback = args[-2]
        self.context = GLib.MainContext.get_thread_default()
        return 1

    def call_sync(self, *args):
        source = GLib.idle_source_new()
        def respond(*_args):
            self.callback(None, None, None, None, None,
                          GLib.Variant("(ua{sv})", (0, {})), None)
            return False
        source.set_callback(respond)
        source.attach(self.context)
        return SimpleNamespace(unpack=lambda: [portal._request_path("probe")])

    def signal_unsubscribe(self, subscription):
        pass

portal._bus = Bus()
portal._new_token = lambda _prefix: "probe"
main_callbacks = []
marker = GLib.idle_add(lambda: main_callbacks.append(threading.get_ident()))
errors = []
def request():
    try:
        assert portal._request("CreateSession", lambda _token: None) == {}
    except Exception as error:
        errors.append(error)

worker = threading.Thread(target=request, daemon=True)
worker.start()
worker.join(timeout=4)
assert not worker.is_alive(), "portal worker did not finish"
assert not errors, errors
assert not main_callbacks, "portal worker dispatched Orca's main context"
GLib.source_remove(marker)
'''
    result = subprocess.run(
        [sys.executable, "-c", script, str(Path(__file__).resolve().parents[1] / "inject.py")],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
