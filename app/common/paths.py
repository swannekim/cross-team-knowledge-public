"""Filesystem locations. Everything the prototype writes stays inside the prototype root."""
from __future__ import annotations

import shutil
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "examples" / "data"
LIBRARY_DIR = DATA_DIR / "team_a_library"
TEAMS_DIR = DATA_DIR / "teams_chat"
CHAT_ONEDRIVE_DIR = TEAMS_DIR / "onedrive_jisoo"
DIRECTORY_FILE = DATA_DIR / "directory.json"
CONTRACT_FILE = DATA_DIR / "sharing_contract.json"
CHAT_EXPORT_FILE = TEAMS_DIR / "chat_export.json"
RESULTS_DIR = ROOT / "results"
ARTIFACTS_DIR = ROOT / "examples" / "artifacts"
SCRATCH_ROOT = ROOT / ".scratch"


def new_scratch_dir(prefix: str) -> Path:
    """Creates a unique scratch directory under <prototype>/.scratch (never the system temp dir)."""
    SCRATCH_ROOT.mkdir(exist_ok=True)
    path = SCRATCH_ROOT / f"{prefix}-{uuid.uuid4().hex[:10]}"
    path.mkdir(parents=True)
    return path


def remove_scratch_dir(path) -> None:
    path = Path(path).resolve()
    if SCRATCH_ROOT.resolve() in path.parents:
        shutil.rmtree(path, ignore_errors=True)
