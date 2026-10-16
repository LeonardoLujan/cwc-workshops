# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0
"""
Local bookkeeping for the cloud resources this app creates. You don't edit
this file — agent.py calls into it.

Why it exists: agents, environments, skills, files and memory stores are
persistent. Create them once, save the ID, reuse it on the next run. And a
whole workshop room may share one API key, so every resource carries your
handle in its name and metadata, and nothing here ever touches a resource
whose ID isn't in your own `.sre-state.json`.

    state.name("commander")   -> "sre-<handle>-commander"
    state.owner()             -> {"workshop": "sre-agent", "owner": "<handle>"}
    state.sync(key, config, create, update)
        first run        -> create()            save the ID
        config unchanged -> nothing             return the saved ID
        config changed   -> update(saved_id)    e.g. a new agent version
"""
import getpass
import hashlib
import json
import os
import re
import secrets
from pathlib import Path
from typing import Any, Callable

STATE_FILE = Path(__file__).parent / ".sre-state.json"
WORKSHOP = "sre-agent"


def _load() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"handle": "", "resources": {}, "retired": []}


def _save(data: dict) -> None:
    STATE_FILE.write_text(json.dumps(data, indent=2) + "\n")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:16]


def handle() -> str:
    """Your short, unique tag. Set SRE_HANDLE in .env to pick it yourself."""
    override = _slug(os.environ.get("SRE_HANDLE", ""))
    data = _load()
    if override and data["handle"] != override:
        data["handle"] = override
        _save(data)
    if not data["handle"]:
        try:
            user = _slug(getpass.getuser())
        except Exception:
            user = ""
        data["handle"] = f"{user or 'sre'}-{secrets.token_hex(2)}"
        _save(data)
    return data["handle"]


def name(kind: str) -> str:
    """Resource name with your handle in it, e.g. sre-ada-4f2c-commander."""
    return f"sre-{handle()}-{kind}"


def owner() -> dict[str, str]:
    """Metadata tag for every resource that accepts metadata."""
    return {"workshop": WORKSHOP, "owner": handle()}


def fingerprint(config: Any) -> str:
    blob = json.dumps(config, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def dir_fingerprint(path: Path) -> str:
    """Changes whenever any file under `path` changes."""
    digest = hashlib.sha256()
    for file in sorted(p for p in Path(path).rglob("*") if p.is_file()):
        digest.update(file.relative_to(path).as_posix().encode())
        digest.update(file.read_bytes())
    return digest.hexdigest()[:16]


def sync(key: str, config: Any, create: Callable[[], Any],
         update: Callable[[str], Any] | None = None) -> str:
    """Create-once, update-on-change. Returns the resource ID.

    `create()` and `update(saved_id)` return the API object (anything with an
    `.id`). If the config changed and there is no `update`, a fresh resource is
    created and the old ID is parked under "retired" for cleanup.py.
    """
    data = _load()
    entry = data["resources"].get(key)
    wanted = fingerprint(config)
    if entry and entry["fingerprint"] == wanted:
        return entry["id"]

    if entry and update:
        result = update(entry["id"])
        resource_id = entry["id"]
    else:
        result = create()
        resource_id = result if isinstance(result, str) else result.id

    data = _load()  # create()/update() may have synced other keys meanwhile
    data["resources"][key] = {
        "id": resource_id,
        "fingerprint": wanted,
        "version": getattr(result, "version", None),
    }
    if entry and not update:
        data["retired"] = sorted(set(data["retired"]) | {entry["id"]})
    _save(data)
    return resource_id


def get(key: str) -> str | None:
    """The saved ID for `key`, or None if it hasn't been created yet."""
    entry = _load()["resources"].get(key)
    return entry["id"] if entry else None


def version(key: str) -> int | None:
    """The saved version for `key` (agents are versioned), if any."""
    entry = _load()["resources"].get(key)
    return entry["version"] if entry else None


def snapshot() -> dict:
    """Everything in the state file (for cleanup.py and check.py)."""
    return _load()


def reset() -> None:
    """Forget everything. cleanup.py calls this after deleting your resources."""
    STATE_FILE.unlink(missing_ok=True)
