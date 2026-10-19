"""Pure deterministic project inventory, not a substitute for model reasoning."""
import hashlib
import json
from pathlib import Path

CANDIDATES = ("README.md", "package.json", "pyproject.toml", "requirements.txt",
              "go.mod", "Cargo.toml", "Makefile", "AGENTS.md")


def inspect_project(root):
    """Small bounded metadata-only scan with no executable code or network calls."""
    root = Path(root).resolve()
    found = []
    test_scripts = []
    for name in CANDIDATES:
        path = root / name
        if path.is_symlink() or not path.is_file():
            continue
        if path.stat().st_size > 128_000:
            continue
        raw = path.read_bytes()
        found.append({"path": name, "bytes": len(raw),
                      "sha256": hashlib.sha256(raw).hexdigest()[:16]})
        if name == "package.json":
            try:
                scripts = json.loads(raw).get("scripts", {})
                if isinstance(scripts, dict):
                    test_scripts = sorted(k for k in scripts if k in (
                        "test", "lint", "typecheck", "build", "check"))
            except (ValueError, AttributeError):
                pass
    return {"files": found, "available_script_names": test_scripts,
            "note": "Inventory only; no tests were executed."}


def execute_read_only_with_retry(tools, name, args, *, sleeper=None):
    """Retry only transient read failures. Never repeat writes or uncertain mutations."""
    if name not in ("list_files", "read_file"):
        return tools.execute(name, args)
    import time
    sleep = sleeper or time.sleep
    for attempt in range(3):
        try:
            return tools.execute(name, args)
        except (TimeoutError, ConnectionError, OSError):
            if attempt == 2:
                raise
            sleep(0.05 * (2 ** attempt))
