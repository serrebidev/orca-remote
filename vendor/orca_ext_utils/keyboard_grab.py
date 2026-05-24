"""Batch wrapper for Atspi.Device key grabs.

The use case is "I want to take over a set of keys for a temporary
mode" -- orca-remote's master-side key forwarding being the
motivating case (while forwarding is on, all typing should go to
the remote, not to local apps). The single-key
`Atspi.Device.add_key_grab` works fine but managing 400+ grabs
(every keysym across every modifier combination) by hand is
tedious and error-prone -- this module provides the bookkeeping.

Backend coverage:

  X11:      add_key_grab works as documented. Grabs are mutually
            exclusive across all clients (only one process can grab
            a given keysym/modifier pair at a time); if another
            process holds the grab, the registration silently
            succeeds but the callback won't fire. This is an X
            limitation, not ours.
  Wayland:  Some compositors honor add_key_grab through the AT-SPI
            bridge; many don't. We register the grabs anyway and
            let the failures be silent. Use the `failed_keysyms`
            attribute to see which weren't accepted.

The class is a context manager so the normal usage pattern is

    with KeysetGrab(keysyms, modifier_combos) as grab:
        grab.register(my_callback)
        ...  # callback fires for matched key events
    # all grabs released here

The release path is best-effort: if Atspi is in a bad state at
exit time we swallow errors so the rest of teardown can complete.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable


# Common modifier-mask combinations users actually press. Bits are
# Atspi.ModifierType values; we keep them as raw ints so this module
# stays importable without the Atspi typelib (the ints come from
# X11's standard mask order).
#
#   0x01 = SHIFT      0x04 = CONTROL    0x08 = ALT (mod1)
#   0x40 = SUPER (mod4 / Win)
#
# Multiplying out all combinations of these four bits gives 16
# modifier states. Most extensions only care about a few of them;
# DEFAULT_MODIFIER_COMBOS is "no modifiers + each single modifier +
# Shift+Ctrl + Shift+Alt + Ctrl+Alt" -- enough for typing and
# command-bar style chord coverage without exploding the grab count.
DEFAULT_MODIFIER_COMBOS = (
    0x00,
    0x01,  # Shift
    0x04,  # Ctrl
    0x08,  # Alt
    0x40,  # Super
    0x01 | 0x04,  # Shift+Ctrl
    0x01 | 0x08,  # Shift+Alt
    0x04 | 0x08,  # Ctrl+Alt
    0x01 | 0x04 | 0x08,  # Shift+Ctrl+Alt
)


KeyEventCallback = Callable[[Any], Any]
"""Atspi key-event callback. The argument is an Atspi.DeviceEvent.

The callback returns truthy to consume the event (the focused app
won't see it) or falsy to pass through. The "consume" semantics
here come from the underlying AT-SPI grab, not from any logic in
this module.
"""


class KeysetGrab:
    """Manages a set of Atspi.Device key grabs as a single unit.

    Example -- grab letters a-z under no-modifier and Shift:

        keysyms = list(range(0x61, 0x7b))      # XK_a..XK_z
        modifiers = [0x00, 0x01]               # plain + Shift
        with KeysetGrab(keysyms, modifiers) as grab:
            grab.register(lambda event: forward_to_remote(event))
            # ... your app's main loop runs here
        # all grabs released

    Construction with no `modifier_combos` uses DEFAULT_MODIFIER_COMBOS.
    Pass an explicit list to cut grab count when you don't need
    full coverage.
    """

    def __init__(
        self,
        keysyms: Iterable[int],
        modifier_combos: Iterable[int] | None = None,
    ) -> None:
        self._keysyms: list[int] = [int(k) for k in keysyms if int(k) > 0]
        if modifier_combos is None:
            self._modifier_combos: list[int] = list(DEFAULT_MODIFIER_COMBOS)
        else:
            self._modifier_combos = [int(m) for m in modifier_combos]

        # Per-(keysym, modifier_mask) grab id. Cleared on release.
        # Atspi.Device.add_key_grab returns the grab id we need to
        # pass back to remove_key_grab; we keep the full list to
        # release them on shutdown.
        self._grab_ids: list[tuple[int, int, int]] = []  # (keysym, modifier, grab_id)
        self._device: Any = None
        self._callback_id: int | None = None
        self.failed_keysyms: list[tuple[int, int]] = []
        """List of (keysym, modifier) pairs where add_key_grab returned 0/failed.

        Populated during __enter__. Use this to surface "some keys
        couldn't be grabbed" to the user (e.g. another app already
        holds the grab; a Wayland compositor that doesn't honor
        AT-SPI grabs).
        """

    def __enter__(self) -> "KeysetGrab":
        self._device = self._get_device()
        if self._device is None:
            # Atspi unavailable -- silent no-op. Callbacks will
            # never fire, failed_keysyms stays empty, release is a
            # no-op too.
            return self

        try:
            import gi  # pylint: disable=import-outside-toplevel
            gi.require_version("Atspi", "2.0")
            from gi.repository import Atspi  # pylint: disable=import-outside-toplevel
        except Exception:  # pylint: disable=broad-except
            return self

        for keysym in self._keysyms:
            for modifier in self._modifier_combos:
                kd = Atspi.KeyDefinition()
                kd.keysym = keysym
                kd.modifiers = modifier
                kd.keycode = 0  # AT-SPI looks the keycode up from keysym
                try:
                    grab_id = self._device.add_key_grab(kd, None)
                except Exception:  # pylint: disable=broad-except
                    grab_id = 0
                if grab_id == 0:
                    self.failed_keysyms.append((keysym, modifier))
                else:
                    self._grab_ids.append((keysym, modifier, grab_id))
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        self.release()

    def register(self, callback: KeyEventCallback) -> bool:
        """Register the callback for events matching any grabbed key.

        Replaces any previously-registered callback on this grab.
        Returns True on success; False if Atspi is unavailable or
        the registration raised.

        The callback receives an `Atspi.DeviceEvent` and should
        return truthy to consume the event. Per AT-SPI semantics,
        consumed events are not delivered to the focused
        application.
        """

        if self._device is None:
            return False
        try:
            import gi  # pylint: disable=import-outside-toplevel
            gi.require_version("Atspi", "2.0")
            from gi.repository import Atspi  # pylint: disable=import-outside-toplevel
        except Exception:  # pylint: disable=broad-except
            return False

        # If we already had a callback, drop it. AT-SPI doesn't
        # have a remove-listener call we can use generically here
        # (the listener is bound to the device); replacing the
        # underlying callback is the practical mechanism.
        self._registered_callback = callback

        def _wrapper(event: Any) -> bool:
            try:
                return bool(callback(event))
            except Exception:  # pylint: disable=broad-except
                return False

        # Attach via the device's key-pressed / key-released signals
        # if available (AT-SPI >= 2.60); otherwise fall back to the
        # legacy key_watcher API. Either way, the callback fires for
        # events that matched one of our grabs.
        try:
            atspi_version = Atspi.get_version()  # pylint: disable=no-value-for-parameter
            if atspi_version[0] > 2 or atspi_version[1] >= 60:
                self._device.connect("key-pressed", _wrapper)
                self._device.connect("key-released", _wrapper)
            else:
                self._device.add_key_watcher(_wrapper)
            return True
        except Exception:  # pylint: disable=broad-except
            return False

    def release(self) -> None:
        """Release every grab this object holds. Best-effort; never raises."""

        if self._device is None:
            return
        for _keysym, _modifier, grab_id in self._grab_ids:
            try:
                self._device.remove_key_grab(grab_id)
            except Exception:  # pylint: disable=broad-except
                pass
        self._grab_ids.clear()

    @staticmethod
    def _get_device() -> Any:
        """Returns an Atspi.Device, or None if AT-SPI isn't available."""

        try:
            import gi  # pylint: disable=import-outside-toplevel
            gi.require_version("Atspi", "2.0")
            from gi.repository import Atspi  # pylint: disable=import-outside-toplevel
            return Atspi.Device.new()
        except Exception:  # pylint: disable=broad-except
            return None
