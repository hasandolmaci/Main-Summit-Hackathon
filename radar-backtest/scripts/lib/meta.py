"""Provenance sidecars: every processed/raw artefact gets <file>.meta.json."""
from __future__ import annotations

import hashlib
import json
import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .config import ROOT


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while b := f.read(chunk):
            h.update(b)
    return h.hexdigest()


def git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception:
        return "unknown"


def write_meta(path: Path, script: str, inputs: list[Path] | None = None, params: dict | None = None,
               rows: int | None = None, sources: list[str] | None = None, notes: str | None = None) -> Path:
    path = Path(path)
    meta = {
        "file": str(path.relative_to(ROOT)) if path.is_absolute() and ROOT in path.parents else str(path),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "script": script,
        "git_sha": git_sha(),
        "python": platform.python_version(),
        "rows": rows,
        "sha256": sha256_file(path) if path.exists() and path.is_file() else None,
        "params": params or {},
        "sources": sources or [],
        "inputs": [
            {"path": str(Path(p).relative_to(ROOT)) if ROOT in Path(p).resolve().parents else str(p),
             "sha256": sha256_file(Path(p)) if Path(p).is_file() else None}
            for p in (inputs or [])
        ],
        "notes": notes,
    }
    out = path.with_name(path.name + ".meta.json")
    out.write_text(json.dumps(meta, indent=2, sort_keys=True))
    return out
