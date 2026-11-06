"""Orca Remote user extension -- package entry point.

Released Orca (51.1) loads a directory in
~/.local/share/orca/extensions/ as an extension package, and reads its
metadata by *parsing* this file. Its reader only accepts a class whose
base is literally named `Extension`, and it takes GROUP_LABEL /
DESCRIPTION / VERSION / AUTHOR / WEBSITE from literal assignments in
that class body. With nothing to find, Orca's extension list shows the
package as "Invalid" and never loads it -- so the class below must stay
a real ClassDef with literal metadata, and its base must keep the name
`Extension` (hence the alias). A plain re-export is not enough.

Newer Orca builds use the manifest.toml entry (entry.module = "remote",
entry.class = "RemoteExtension") and never execute this file. Both
loaders end up with the same RemoteExtension implementation.
"""

try:
    # Under Orca: the real implementation, aliased so the base name
    # matches what Orca's metadata reader looks for.
    from .remote import RemoteExtension as Extension
except ImportError:
    # Imported standalone (pytest, linters, a plain REPL) where the
    # orca package is absent. The class body must still parse and the
    # module must still import; Orca only reads this branch's metadata.
    Extension = object  # type: ignore[assignment,misc]


class OrcaRemoteExtension(Extension):  # type: ignore[misc,valid-type]
    """Metadata carrier for released Orca's extension list."""

    GROUP_LABEL = "Orca Remote"
    DESCRIPTION = (
        "NVDA-Remote-compatible remote access for Orca: speech and braille "
        "mirroring over an NVDA Remote v2 relay, plus remote key control."
    )
    VERSION = "0.9.12"
    AUTHOR = "SerrebiDev and contributors"
    ORGANIZATION = ""
    COPYRIGHT = ""
    WEBSITE = "https://github.com/serrebidev/orca-remote"
