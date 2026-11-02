"""Separate a one-file executable's bundled resources from persistent user data."""
from __future__ import annotations
import os
from pathlib import Path
import sys


def resource_dir():
    # PyInstaller sets __file__ relative to its extracted bundle directory.
    return Path(__file__).resolve().parent


def data_dir():
    if not getattr(sys, 'frozen', False):
        return resource_dir()
    override = os.environ.get('MANOSABA_DATA_DIR')
    if override:
        return Path(override).expanduser().resolve()
    local = Path(os.environ.get('LOCALAPPDATA') or Path.home()/'AppData'/'Local')
    return local/'ManosabaStudio'
