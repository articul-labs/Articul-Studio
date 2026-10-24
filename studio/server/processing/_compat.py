"""Availability plumbing for the vendored speechscore/clearvoice packages.

Both packages are vendored at the repo root (outside ``studio/``) and pull in
heavy optional deps (torch, librosa, onnxruntime). Nothing here imports them
at module import time: sys.path entries are added lazily and availability is
probed inside functions, so importing this module stays cheap and
side-effect free.
"""
from __future__ import annotations

import importlib
import shutil
import sys
from pathlib import Path


class ProcessingUnavailable(RuntimeError):
    """Raised when a processing wrapper runs without its dependencies."""


def repo_root() -> Path:
    """Repo root: the directory holding the vendored speechscore/ and clearvoice/."""
    # This file lives at <root>/studio/server/processing/_compat.py
    return Path(__file__).resolve().parents[3]


_paths_ready = False


def ensure_repo_paths() -> None:
    """Put the vendored packages on sys.path (idempotent, once per process).

    The speechscore/ directory itself must be on sys.path because its modules
    import ``scores.*`` and ``basis`` as top-level names. Inserting it first
    also makes ``import speechscore`` resolve to the module speechscore.py
    instead of the speechscore/ package, whose __init__ imports modules that
    do not exist.
    """
    global _paths_ready
    if _paths_ready:
        return
    root = repo_root()
    for name in ("speechscore", "clearvoice"):
        entry = str(root / name)
        if entry not in sys.path:
            sys.path.insert(0, entry)
    _paths_ready = True


def _import_error(module: str) -> str | None:
    """Try importing a vendored module; return a readable error or None."""
    try:
        ensure_repo_paths()
        importlib.import_module(module)
        return None
    except Exception as exc:  # any failure counts as unavailable, not just ImportError
        return f"{type(exc).__name__}: {exc}"


def speechscore_available() -> bool:
    return _import_error("speechscore") is None


def clearvoice_available() -> bool:
    return _import_error("clearvoice") is None


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def reason() -> str:
    """Human-readable summary of exactly what is missing for processing."""
    problems = []
    for module, label in (("speechscore", "speechscore/DNSMOS"), ("clearvoice", "clearvoice")):
        error = _import_error(module)
        if error is not None:
            problems.append(f"{label} not importable ({error})")
    if not ffmpeg_available():
        problems.append("ffmpeg not on PATH")
    if not problems:
        return "all processing dependencies available"
    return "processing unavailable: " + "; ".join(problems)
