"""Terminal helpers for single-key input (macOS/Linux only).

The run loop is the *only* consumer of stdin, so we expose a cbreak context
manager plus a non-blocking ``read_key`` rather than a background thread. This
avoids two readers fighting over stdin when we need to prompt the user.
"""

import contextlib
import select
import sys

try:
    import termios
    import tty

    _HAVE_TTY = True
except ImportError:  # pragma: no cover - non-unix
    _HAVE_TTY = False


def interactive() -> bool:
    return _HAVE_TTY and sys.stdin.isatty()


@contextlib.contextmanager
def cbreak_terminal():
    """Put stdin into cbreak mode for the duration of the block.

    Yields True if cbreak is active, False when stdin is not an interactive
    tty (piped input). The original terminal settings are always restored.
    """
    if not interactive():
        yield False
        return
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        yield True
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def read_key(timeout: float = 0.3):
    """Return a single character if one is available within ``timeout``, else None."""
    if not interactive():
        return None
    try:
        ready, _, _ = select.select([sys.stdin], [], [], timeout)
    except (OSError, ValueError):
        return None
    if not ready:
        return None
    try:
        return sys.stdin.read(1)
    except (OSError, ValueError):
        return None


def read_key_blocking():
    """Block until a single key is pressed and return it."""
    while True:
        ch = read_key(0.5)
        if ch is not None:
            return ch
