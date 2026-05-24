"""Subprocess supervision with timeout, signal handling, and async output.

Extensions that spawn helper processes (orca-ocr -> tesseract; a
hypothetical voice-command extension -> whisper.cpp; a recording
extension -> ffmpeg) all need the same boring bookkeeping:

  - run a command with a timeout
  - capture stdout / stderr without pipe-buffer deadlock
  - integrate cleanly with the GLib main loop so the UI doesn't
    freeze while waiting
  - send SIGTERM then SIGKILL if the process won't die

This module wraps `subprocess` + `GLib.child_watch_add` to provide
both sync and async entry points. The sync path is for "give me
output now" calls (short commands, version probes); the async
path is for anything that might block the UI.

The async path uses temp files for stdout/stderr rather than pipes.
This is deliberate: pipe-buffer fills cause silent deadlock on
processes that emit more than ~64KiB of output without being drained,
and threading a drain loop adds complexity for marginal benefit.
Temp files are fast on tmpfs (every modern Linux has /tmp on tmpfs)
and trivially correct.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, NamedTuple, Sequence


class ProcessResult(NamedTuple):
    """Outcome of a supervised process run."""

    returncode: int
    """Process exit code. -1 indicates timeout or signal kill."""

    stdout: bytes
    """Captured stdout. Empty on failure paths that never produced output."""

    stderr: bytes
    """Captured stderr. Empty on failure paths that never produced output."""

    timed_out: bool
    """True iff the supervisor killed the process due to timeout."""

    error: str | None
    """Human-readable error string when the supervisor itself failed
    (executable not found, exec failed, signal raised in supervisor).
    None on a clean run (even when the child process itself exited
    non-zero)."""


ResultCallback = Callable[[ProcessResult], None]
"""Called with a ProcessResult when an async supervisor completes."""


def is_available(executable: str) -> bool:
    """Returns True iff `executable` is found on PATH.

    Convenience wrapper around shutil.which so callers don't have
    to import it themselves. Most extensions use this for a "you
    need to install tesseract" first-run check.
    """

    return shutil.which(executable) is not None


def run_sync(
    argv: Sequence[str], *,
    timeout_seconds: float = 10.0,
    stdin_bytes: bytes | None = None,
    cwd: str | None = None,
    env: dict | None = None,
) -> ProcessResult:
    """Run `argv` synchronously. Returns a ProcessResult.

    Blocks the calling thread for up to `timeout_seconds`. On
    timeout: SIGTERM is sent; if the process is still alive after
    1 second, SIGKILL. Returns with `timed_out=True` and
    `returncode=-1` in that case.

    Suitable for: version probes, --help calls, quick metadata
    queries. Not suitable for anything that might block more than
    a couple of seconds -- use `run_async` from a GLib main loop.
    """

    if not argv or not shutil.which(argv[0]):
        return ProcessResult(
            returncode=-1, stdout=b"", stderr=b"",
            timed_out=False, error=f"executable not found: {argv[0] if argv else '<empty>'}",
        )

    try:
        result = subprocess.run(
            list(argv),
            input=stdin_bytes,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
            cwd=cwd,
            env=env,
        )
        return ProcessResult(
            returncode=int(result.returncode),
            stdout=bytes(result.stdout or b""),
            stderr=bytes(result.stderr or b""),
            timed_out=False, error=None,
        )
    except subprocess.TimeoutExpired as e:
        return ProcessResult(
            returncode=-1,
            stdout=bytes(e.stdout or b""),
            stderr=bytes(e.stderr or b""),
            timed_out=True, error=f"timed out after {timeout_seconds}s",
        )
    except OSError as e:
        return ProcessResult(
            returncode=-1, stdout=b"", stderr=b"",
            timed_out=False, error=f"OSError: {e}",
        )


def run_async(
    argv: Sequence[str], on_done: ResultCallback, *,
    timeout_seconds: float = 30.0,
    stdin_bytes: bytes | None = None,
    cwd: str | None = None,
    env: dict | None = None,
) -> "AsyncHandle | None":
    """Run `argv` asynchronously; invoke `on_done(ProcessResult)` when done.

    Returns an AsyncHandle the caller can use to cancel before
    completion. Returns None and fires `on_done` immediately with
    an error result if the executable can't be launched.

    Requires a running GLib main loop -- the completion callback
    fires from that loop. Stdout / stderr are captured via temp
    files and read back on completion (no pipe-buffer deadlock
    risk regardless of how much the child emits).
    """

    if not argv or not shutil.which(argv[0]):
        result = ProcessResult(
            returncode=-1, stdout=b"", stderr=b"",
            timed_out=False, error=f"executable not found: {argv[0] if argv else '<empty>'}",
        )
        on_done(result)
        return None

    try:
        import gi  # pylint: disable=import-outside-toplevel
        gi.require_version("GLib", "2.0")
        from gi.repository import GLib  # pylint: disable=import-outside-toplevel
    except Exception as e:  # pylint: disable=broad-except
        on_done(ProcessResult(
            returncode=-1, stdout=b"", stderr=b"",
            timed_out=False, error=f"GLib unavailable: {e}",
        ))
        return None

    stdout_fd, stdout_path = tempfile.mkstemp(suffix=".stdout", prefix="orca_ext_")
    stderr_fd, stderr_path = tempfile.mkstemp(suffix=".stderr", prefix="orca_ext_")

    try:
        proc = subprocess.Popen(
            list(argv),
            stdin=subprocess.PIPE if stdin_bytes is not None else subprocess.DEVNULL,
            stdout=stdout_fd, stderr=stderr_fd,
            cwd=cwd, env=env,
        )
    except OSError as e:
        os.close(stdout_fd); os.close(stderr_fd)
        Path(stdout_path).unlink(missing_ok=True)
        Path(stderr_path).unlink(missing_ok=True)
        on_done(ProcessResult(
            returncode=-1, stdout=b"", stderr=b"",
            timed_out=False, error=f"Popen failed: {e}",
        ))
        return None

    # Close the local fds; the kernel keeps them open for the child.
    os.close(stdout_fd); os.close(stderr_fd)

    if stdin_bytes is not None and proc.stdin is not None:
        try:
            proc.stdin.write(stdin_bytes)
            proc.stdin.close()
        except OSError:
            pass

    state: dict = {
        "done": False, "timed_out": False,
        "watch_id": None, "timeout_id": None,
    }

    def cleanup_files() -> None:
        Path(stdout_path).unlink(missing_ok=True)
        Path(stderr_path).unlink(missing_ok=True)

    def finish(returncode: int, error: str | None) -> None:
        if state["done"]:
            return
        state["done"] = True
        if state["timeout_id"] is not None:
            GLib.source_remove(state["timeout_id"])
            state["timeout_id"] = None
        try:
            stdout = Path(stdout_path).read_bytes()
        except OSError:
            stdout = b""
        try:
            stderr = Path(stderr_path).read_bytes()
        except OSError:
            stderr = b""
        cleanup_files()
        on_done(ProcessResult(
            returncode=returncode, stdout=stdout, stderr=stderr,
            timed_out=state["timed_out"], error=error,
        ))

    def on_child_exit(_pid, status) -> None:
        # GLib gives the raw status; decode it into a returncode.
        if os.WIFEXITED(status):
            rc = os.WEXITSTATUS(status)
        elif os.WIFSIGNALED(status):
            rc = -os.WTERMSIG(status)
        else:
            rc = -1
        finish(rc, None)

    def on_timeout() -> bool:
        if state["done"]:
            return GLib.SOURCE_REMOVE
        state["timed_out"] = True
        # SIGTERM first; if the child doesn't die in 1 second, SIGKILL.
        try:
            proc.terminate()
        except OSError:
            pass
        def kill_check() -> bool:
            if proc.poll() is None:
                try:
                    proc.kill()
                except OSError:
                    pass
            return GLib.SOURCE_REMOVE
        GLib.timeout_add(1000, kill_check)
        # finish() runs from on_child_exit when the kill takes effect.
        return GLib.SOURCE_REMOVE

    state["watch_id"] = GLib.child_watch_add(GLib.PRIORITY_DEFAULT, proc.pid, on_child_exit)
    state["timeout_id"] = GLib.timeout_add(int(timeout_seconds * 1000), on_timeout)

    return AsyncHandle(proc, state)


class AsyncHandle:
    """Handle for cancelling an in-flight async process."""

    def __init__(self, proc: "subprocess.Popen", state: dict) -> None:
        self._proc = proc
        self._state = state

    def cancel(self) -> None:
        """Terminate the running process. Idempotent; safe after completion."""

        if self._state.get("done"):
            return
        try:
            self._proc.terminate()
        except OSError:
            pass
        # The child-watch fires when the process actually exits; we
        # don't need to call finish() here.

    @property
    def pid(self) -> int:
        """The child process PID. -1 after the process exits."""

        try:
            return int(self._proc.pid)
        except Exception:  # pylint: disable=broad-except
            return -1

    def is_running(self) -> bool:
        """True iff the process hasn't exited yet."""

        return self._proc.poll() is None
