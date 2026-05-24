"""Per-extension settings persistence.

Every extension wants the same thing: "remember what the user
picked the last time they opened my settings dialog." Doing this
right is non-trivial -- GSettings needs a schema file installed in
the right place; raw JSON in a dotfile means writing locking and
schema-migration code; a Python `configparser` ini gets you most
of the way but loses type information.

This module provides a small key-value store backed by GSettings
when an extension-specific schema is installed, and by a simple
JSON file under `$XDG_CONFIG_HOME/orca/extensions/<name>.json`
otherwise. Same API either way; extensions don't need to know
which backend is in use.

Type handling:

  - Strings, bools, ints, and floats round-trip losslessly through
    both backends.
  - Lists and dicts work through the JSON backend; through GSettings
    they require the schema to declare them as variant types.
  - Anything else (custom classes, callables) raises ValueError on
    set. Serialize it yourself before storing.

Concurrency: the JSON backend uses an atomic write (write to temp,
rename into place) so a crash mid-write can't corrupt the file.
Concurrent reads from a Python process and an external editor will
see one or the other version, never a partial write. Multiple
Python processes hitting the same file race; if you need cross-
process consistency, ship a GSettings schema.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable


class Settings:
    """A key-value store for a single extension's settings.

    Construct with the extension's short name (matches the .orca-ext
    manifest name, not the human-readable one). The store is
    instantiated lazily on first access; constructing a Settings
    object never touches disk.
    """

    def __init__(
        self, extension_name: str, *,
        defaults: dict[str, Any] | None = None,
    ) -> None:
        self._name = extension_name
        self._defaults: dict[str, Any] = dict(defaults or {})
        self._gsettings = None
        self._gsettings_probed = False
        self._json_cache: dict[str, Any] | None = None

    def get(self, key: str, default: Any = None) -> Any:
        """Returns the value for `key`, falling back through declared and explicit defaults.

        Lookup order:
          1. Stored value (whatever the user previously set).
          2. Instance defaults dict passed at construction.
          3. Explicit `default` argument.
          4. None.

        The instance defaults dict is for "library author says, this
        is what the setting means when not yet customized." The
        explicit `default` arg is for "caller doesn't know if the
        setting exists; here's what to return if nothing's known."
        """

        gsettings = self._get_gsettings()
        if gsettings is not None:
            try:
                value = gsettings.get_value(key)
                return _gvariant_to_python(value)
            except Exception:  # pylint: disable=broad-except
                pass

        data = self._read_json()
        if key in data:
            return data[key]
        if key in self._defaults:
            return self._defaults[key]
        return default

    def set(self, key: str, value: Any) -> bool:
        """Persist `key`=`value`. Returns True on success.

        Raises ValueError if `value` isn't a JSON-representable
        scalar / list / dict (the only types both backends handle
        without per-extension setup).
        """

        if not _is_serializable(value):
            raise ValueError(
                f"value for {key!r} is not serializable: {type(value).__name__}"
            )

        gsettings = self._get_gsettings()
        if gsettings is not None:
            try:
                gvariant = _python_to_gvariant(value)
                if gvariant is not None:
                    gsettings.set_value(key, gvariant)
                    return True
            except Exception:  # pylint: disable=broad-except
                pass

        data = self._read_json()
        data[key] = value
        return self._write_json(data)

    def delete(self, key: str) -> bool:
        """Remove `key` from the store. True if it was present, False otherwise."""

        gsettings = self._get_gsettings()
        if gsettings is not None:
            try:
                gsettings.reset(key)
                return True
            except Exception:  # pylint: disable=broad-except
                pass

        data = self._read_json()
        if key in data:
            del data[key]
            self._write_json(data)
            return True
        return False

    def keys(self) -> Iterable[str]:
        """Yields the keys currently set in the store.

        Defaults that haven't been overridden are NOT included --
        only keys with a stored value. Use `get(key)` to read a
        default-only key.
        """

        gsettings = self._get_gsettings()
        if gsettings is not None:
            try:
                return tuple(gsettings.list_keys())
            except Exception:  # pylint: disable=broad-except
                pass
        return tuple(self._read_json().keys())

    def backend(self) -> str:
        """Returns "gsettings" or "json" based on what this instance is using."""

        return "gsettings" if self._get_gsettings() is not None else "json"

    def _get_gsettings(self):
        if self._gsettings_probed:
            return self._gsettings
        self._gsettings_probed = True
        schema_id = f"org.gnome.orca.extensions.{self._name}"
        try:
            import gi  # pylint: disable=import-outside-toplevel
            gi.require_version("Gio", "2.0")
            from gi.repository import Gio  # pylint: disable=import-outside-toplevel
            source = Gio.SettingsSchemaSource.get_default()
            if source is None:
                return None
            schema = source.lookup(schema_id, recursive=True)
            if schema is None:
                return None
            self._gsettings = Gio.Settings.new(schema_id)
            return self._gsettings
        except Exception:  # pylint: disable=broad-except
            return None

    def _json_path(self) -> Path:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
        return Path(base) / "orca" / "extensions" / f"{self._name}.json"

    def _read_json(self) -> dict[str, Any]:
        if self._json_cache is not None:
            return self._json_cache
        path = self._json_path()
        try:
            self._json_cache = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self._json_cache = {}
        except (OSError, json.JSONDecodeError):
            # Corrupt file: start fresh, but don't clobber it yet --
            # the user might want to recover by hand. Caller's first
            # `set` will overwrite via the atomic-write path.
            self._json_cache = {}
        if not isinstance(self._json_cache, dict):
            self._json_cache = {}
        return self._json_cache

    def _write_json(self, data: dict[str, Any]) -> bool:
        path = self._json_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            return False
        # Atomic write: temp file in the same directory, then rename.
        # Same-directory placement so the rename stays cheap (no
        # cross-filesystem copy).
        try:
            fd, tmp_path = tempfile.mkstemp(
                prefix=f".{self._name}.", suffix=".json.tmp",
                dir=str(path.parent),
            )
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, sort_keys=True)
            os.replace(tmp_path, path)
            self._json_cache = dict(data)
            return True
        except OSError:
            return False


def _is_serializable(value: Any) -> bool:
    if value is None or isinstance(value, (bool, int, float, str)):
        return True
    if isinstance(value, (list, tuple)):
        return all(_is_serializable(v) for v in value)
    if isinstance(value, dict):
        return all(isinstance(k, str) and _is_serializable(v) for k, v in value.items())
    return False


def _python_to_gvariant(value: Any):
    """Convert a Python value to a GLib.Variant of the matching type.

    Returns None when the value's type doesn't have a single
    obvious GVariant representation (callers fall back to JSON).
    """

    try:
        import gi  # pylint: disable=import-outside-toplevel
        gi.require_version("GLib", "2.0")
        from gi.repository import GLib  # pylint: disable=import-outside-toplevel
    except Exception:  # pylint: disable=broad-except
        return None

    if isinstance(value, bool):
        return GLib.Variant("b", value)
    if isinstance(value, int):
        return GLib.Variant("i", int(value))
    if isinstance(value, float):
        return GLib.Variant("d", float(value))
    if isinstance(value, str):
        return GLib.Variant("s", value)
    if isinstance(value, (list, tuple)) and all(isinstance(v, str) for v in value):
        return GLib.Variant("as", list(value))
    # Other types (mixed lists, dicts) are handled JSON-side rather
    # than trying to guess a GVariant type signature.
    return None


def _gvariant_to_python(value):
    """Convert a GLib.Variant back to a plain Python value."""

    try:
        return value.unpack()
    except Exception:  # pylint: disable=broad-except
        return None
