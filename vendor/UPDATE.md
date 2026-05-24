# Vendored dependencies

This directory contains third-party Python modules vendored into
orca-remote for shipping in the `.orca-ext` archive. End users
don't need pip; the archive contains everything.

## orca_ext_utils

- **Upstream:** <https://github.com/churst90/orca-ext-utils>
- **Synced from version:** `v0.2.0` (commit `0139105`)
- **Last synced:** 2026-05-21
- **What we use from it:**
  - `keyboard_grab.KeysetGrab` — full system-level key consume
    while master-mode forwarding is active (so forwarded keys
    don't also type into the focused local app).
  - `_backend` — pulled in transitively by `keyboard_grab`.

## Re-sync procedure

```sh
cd ~/dev/orca-ext-utils && git pull
cp ~/dev/orca-ext-utils/orca_ext_utils/*.py \
   ~/dev/orca-remote/vendor/orca_ext_utils/
# Update the "Synced from version" line above with the new tag /
# commit, then commit the change.
```

Do not edit files under `vendor/orca_ext_utils/` directly. Fix
bugs upstream and re-sync. Local edits get clobbered on the next
sync and are easy to lose track of.
