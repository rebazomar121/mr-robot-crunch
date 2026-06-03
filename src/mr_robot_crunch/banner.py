"""ANSI banner and colored-text helpers (no third-party dependency)."""

import os
import sys

from . import __author__, __github__, __version__

# --- ANSI colors -----------------------------------------------------------
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
MAGENTA = "\033[95m"
WHITE = "\033[97m"


def _supports_color() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return sys.stdout.isatty()


_COLOR = _supports_color()


def c(text: str, *codes: str) -> str:
    """Wrap ``text`` in ANSI ``codes`` when the terminal supports color."""
    if not _COLOR or not codes:
        return text
    return "".join(codes) + text + RESET


def red(text: str) -> str:
    return c(text, BOLD, RED)


def green(text: str) -> str:
    return c(text, GREEN)


def cyan(text: str) -> str:
    return c(text, CYAN)


def yellow(text: str) -> str:
    return c(text, YELLOW)


def dim(text: str) -> str:
    return c(text, DIM)


_ASCII = r"""
  __  __ ___   ___  ___  ___  ___ _____    ___ ___ _   _ _  _  ___ _  _
 |  \/  | _ \ | _ \/ _ \| _ )/ _ \_   _|  / __| _ \ | | | \| |/ __| || |
 | |\/| |   / |   / (_) | _ \ (_) || |   | (__|   / |_| | .` | (__| __ |
 |_|  |_|_|_\ |_|_\\___/|___/\___/ |_|    \___|_|_\\___/|_|\_|\___|_||_|
"""


def render_banner() -> str:
    """Return the full startup banner string."""
    art = c(_ASCII, BOLD, MAGENTA)
    lines = [art, c(f"  author : {__author__}", BOLD, CYAN)]
    if __github__:
        lines.append(c(f"  github : {__github__}", CYAN))
    lines.append(dim(f"  version: {__version__}"))
    lines.append(dim("  combine words into every possible mutation — fsociety style"))
    return "\n".join(lines) + "\n"


def print_banner() -> None:
    print(render_banner())
