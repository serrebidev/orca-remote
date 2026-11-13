"""Keep the test run away from the real user's Orca files.

Several modules compute ~/.local/share/orca/... paths at import time
(the debug log, settings, the fingerprint file). Point HOME and
XDG_DATA_HOME at a throwaway directory before any test module imports
them, so a test run never writes into the developer's live Orca setup.
"""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile

_TMP_HOME = tempfile.mkdtemp(prefix="orca-remote-test-home-")
os.environ["HOME"] = _TMP_HOME
os.environ["XDG_DATA_HOME"] = os.path.join(_TMP_HOME, ".local", "share")
os.makedirs(os.path.join(os.environ["XDG_DATA_HOME"], "orca"), exist_ok=True)
atexit.register(shutil.rmtree, _TMP_HOME, ignore_errors=True)
