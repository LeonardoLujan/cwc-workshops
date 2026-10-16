# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0
"""
Remove the cloud resources this app created for you. Nothing else.

    python cleanup.py --dry-run   # show what would be removed
    python cleanup.py             # remove it, then forget the local state

It only looks at IDs recorded in your own .sre-state.json, and it checks each
one still belongs to your handle before touching it. That matters when a room
shares one API key: it can never remove someone else's agent.
"""
import argparse
import sys
from pathlib import Path

import anthropic

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

import state  # noqa: E402
import provided  # noqa: E402,F401  (importing it loads .env)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    data = state.snapshot()
    me = data.get("handle")
    ids = [entry["id"] for entry in data.get("resources", {}).values()] + list(data.get("retired", []))
    if not me or not ids:
        print("nothing recorded in .sre-state.json, nothing to do")
        return 0
    client = anthropic.Anthropic()
    beta = client.beta

    def mine(resource) -> bool:
        return (getattr(resource, "metadata", None) or {}).get("owner") == me

    def act(label: str, resource_id: str, ok: bool, do) -> None:
        if not ok:
            print(f"  skip    {label:12} {resource_id}  (not tagged with your handle)")
        elif args.dry_run:
            print(f"  would   {label:12} {resource_id}")
        else:
            try:
                do()
                print(f"  removed {label:12} {resource_id}")
            except anthropic.APIError as exc:
                print(f"  FAILED  {label:12} {resource_id}  {getattr(exc, 'message', exc)}")

    def guarded(label: str, resource_id: str, fetch, check, do) -> None:
        try:
            resource = fetch()
        except anthropic.NotFoundError:
            print(f"  gone    {label:12} {resource_id}")
            return
        except anthropic.APIError as exc:
            print(f"  FAILED  {label:12} {resource_id}  {getattr(exc, 'message', exc)}")
            return
        act(label, resource_id, check(resource), do)

    print(f"handle: {me}{'   (dry run)' if args.dry_run else ''}")
    agents = [i for i in ids if i.startswith("agent_")]

    # Sessions first: an environment can't be deleted while sessions reference it.
    for agent_id in agents:
        try:
            sessions = list(beta.sessions.list(agent_id=agent_id, limit=100))
        except anthropic.APIError:
            sessions = []
        for session in sessions:
            act("session", session.id, mine(session), lambda s=session: beta.sessions.delete(s.id))

    for rid in ids:
        if rid.startswith("agent_"):  # agents are archived, not deleted
            guarded("agent", rid, lambda: beta.agents.retrieve(rid), mine, lambda: beta.agents.archive(rid))
        elif rid.startswith("memstore_"):
            guarded("memory store", rid, lambda: beta.memory_stores.retrieve(rid), mine,
                    lambda: beta.memory_stores.delete(rid))
        elif rid.startswith("skill_"):  # no metadata on skills: match the title this app gave it

            def remove_skill(skill_id=rid):
                for version in beta.skills.versions.list(skill_id):
                    beta.skills.versions.delete(version.version, skill_id=skill_id)
                beta.skills.delete(skill_id)

            guarded("skill", rid, lambda: beta.skills.retrieve(rid),
                    lambda s: (getattr(s, "display_title", "") or "").endswith(f"({me})"), remove_skill)
        elif rid.startswith("file_"):  # no metadata on files: match the one filename this app uploads
            guarded("file", rid, lambda: beta.files.retrieve_metadata(rid),
                    lambda f: getattr(f, "filename", "") == "app.log", lambda: beta.files.delete(rid))
    for rid in ids:
        if rid.startswith("env_"):
            guarded("environment", rid, lambda: beta.environments.retrieve(rid), mine,
                    lambda: beta.environments.delete(rid))

    if not args.dry_run:
        state.reset()
        print("local state forgotten (.sre-state.json removed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
