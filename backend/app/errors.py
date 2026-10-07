"""Safe diagnostics for exceptions that may contain meeting data or credentials."""

from __future__ import annotations

import re


def safe_error_code(error: object) -> str:
    """Keep exception type for diagnosis without retaining its potentially sensitive message."""
    if not isinstance(error, BaseException):
        return "Error"
    name = type(error).__name__
    return name if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", name) else "Error"
