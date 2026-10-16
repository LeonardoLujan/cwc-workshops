# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0
"""
Headless check of your agent.py against the live API. No Streamlit.

    python check.py              # every part you have implemented so far
    python check.py --part 1     # stop after one part (1-4)
    python check.py --solution   # run the reference in solutions/agent.py

It drives the same functions the dashboard calls, prints a compact event log,
and ends with PASS or FAIL per part. The session it creates stays in your
session picker so you can open it in the dashboard afterwards.
"""
import argparse
import logging
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
DEADLINE_S = 15 * 60
logging.getLogger("streamlit").setLevel(logging.ERROR)  # "No runtime found" is expected outside the dashboard


def text_of(content) -> str:
    return "".join(getattr(b, "text", "") for b in content or [] if getattr(b, "type", None) == "text")


def short(value, limit=110) -> str:
    value = " ".join(str(value).split())
    return value if len(value) <= limit else value[: limit - 1] + "…"


def run_turn(agent, session_id: str, prompt: str) -> dict:
    """Send one message, print what happens, return what was observed."""
    started = time.monotonic()
    seen = {"text": "", "threads": set(), "sandbox": 0, "local": 0, "types": {}, "stop": None}

    def log(line):
        print(f"  {time.monotonic() - started:5.0f}s  {line}")

    print(f"\n> {prompt}")
    for ev in agent.stream_reply(session_id, prompt):
        seen["types"][ev.type] = seen["types"].get(ev.type, 0) + 1
        thread = getattr(ev, "session_thread_id", None)
        where = f"[{thread[-6:]}] " if thread else ""
        if ev.type == "agent.message":
            seen["text"] += text_of(ev.content) + "\n"
            log(f"agent: {short(text_of(ev.content))}")
        elif ev.type == "agent.tool_use":
            seen["sandbox"] += 1
            log(f"{where}sandbox · {ev.name}  {short(ev.input, 80)}")
        elif ev.type == "agent.custom_tool_use":
            seen["local"] += 1
            log(f"{where}local · {ev.name}  {short(ev.input, 80)}")
        elif ev.type == "session.thread_created":
            seen["threads"].add(ev.session_thread_id)
            log(f"thread created [{ev.session_thread_id[-6:]}] {ev.agent_name}")
        elif ev.type == "agent.thread_message_sent":
            log(f"brief  -> [{ev.to_session_thread_id[-6:]}] {short(text_of(ev.content), 90)}")
        elif ev.type == "agent.thread_message_received":
            log(f"report <- [{ev.from_session_thread_id[-6:]}] {short(text_of(ev.content), 90)}")
        elif ev.type == "session.error":
            log(f"SESSION ERROR: {short(getattr(getattr(ev, 'error', None), 'message', ev), 200)}")
        elif ev.type == "session.status_idle":
            seen["stop"] = ev.stop_reason.type
        elif ev.type == "session.status_terminated":
            seen["stop"] = "terminated"
        if time.monotonic() - started > DEADLINE_S:
            log("TIMEOUT")
            seen["stop"] = "timeout"
            break
    seen["seconds"] = round(time.monotonic() - started)
    log(f"done: stop={seen['stop']}  threads={len(seen['threads'])}  "
        f"sandbox={seen['sandbox']}  local={seen['local']}")
    return seen


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--part", type=int, choices=[1, 2, 3, 4], help="stop after this part")
    parser.add_argument("--solution", action="store_true", help="check solutions/agent.py instead of agent.py")
    args = parser.parse_args()
    if args.solution:
        os.environ["SRE_AGENT_MODULE"] = "solutions.agent"
    sys.path.insert(0, str(HERE))

    import state
    from provided import implemented, load_agent, safe_filename

    try:
        agent = load_agent()
    except Exception as exc:  # most often: no API key in .env
        print(f"could not import the agent module: {exc}")
        return 1
    print(f"module: {agent.__name__}   handle: {state.handle()}")

    results: dict[str, bool] = {}

    def verdict(name: str, ok: bool, detail: str = "") -> bool:
        results[name] = ok
        print(f"{'PASS' if ok else 'FAIL'}  {name}{'  — ' + detail if detail else ''}")
        return ok

    # ── setup: every function Part 1 needs ────────────────────────────────
    ids = {}
    for fn in ("setup_environment", "upload_log", "setup_agent"):
        try:
            ids[fn] = getattr(agent, fn)()
            print(f"  {fn:18} {ids[fn]}")
        except NotImplementedError:
            print(f"  {fn:18} not implemented yet")
            return 1 if not verdict("part 1 · one agent", False, f"implement {fn}() in agent.py") else 0
    has = {
        2: implemented(agent.setup_investigator),
        3: implemented(agent.setup_memory),
        4: implemented(agent.download_outputs),
    }
    last = args.part or max([1] + [part for part, on in has.items() if on])
    print("  parts implemented: 1" + "".join(f", {p}" for p, on in has.items() if on) + f"   checking up to part {last}")

    session_id = agent.start_session(ids["setup_agent"], ids["setup_environment"], ids["upload_log"])
    print(f"  session            {session_id}")

    # ── part 1: one incident, one agent ───────────────────────────────────
    if last == 1 or not has[2]:
        seen = run_turn(agent, session_id, "INC-2277: checkout p99 latency spiked around 14:32 UTC. Find the root cause.")
        reply = seen["text"].lower()
        verdict("part 1 · one agent", seen["stop"] == "end_turn" and "a3f9c21" in reply
                and ("n+1" in reply or "n + 1" in reply or "per-order" in reply or "per order" in reply),
                f"{seen['seconds']}s, named a3f9c21: {'a3f9c21' in reply}")
    else:
        # ── part 2: three pages, one investigator each ────────────────────
        # "Treat them as new" keeps this a multiagent run even when the notebook
        # (Part 3) already holds notes from an earlier run.
        seen = run_turn(agent, session_id, "Three pages are open. Treat them as new and run the incident: "
                                           "one investigator per page.")
        reply = seen["text"].lower()
        verdict("part 1 · one agent", seen["stop"] == "end_turn", "the stream loop ran to end_turn")
        linked = "db-primary" in reply or "symptom" in reply or "same incident" in reply or "same root cause" in reply
        verdict("part 2 · multiagent",
                len(seen["threads"]) >= 2 and "a3f9c21" in reply and "c81d4e7" in reply and linked,
                f"{seen['seconds']}s, {len(seen['threads'])} threads, a3f9c21: {'a3f9c21' in reply}, "
                f"c81d4e7: {'c81d4e7' in reply}, cart linked to checkout: {linked}")

    # ── part 3: memory stores ─────────────────────────────────────────────
    if has[3] and last >= 3:
        session = agent.client.beta.sessions.retrieve(session_id)
        mounted = [r for r in session.resources or [] if r.type == "memory_store"]
        notebook = state.get("memory:notebook")
        notes = []
        if notebook:
            for _ in range(3):
                notes = [m for m in agent.client.beta.memory_stores.memories.list(notebook, path_prefix="/")
                         if getattr(m, "type", "") == "memory"]
                if notes:
                    break
                time.sleep(2)
        verdict("part 3 · memory stores", len(mounted) >= 2 and len(notes) >= 1,
                f"{len(mounted)} stores mounted, {len(notes)} notes in the notebook"
                + (f" ({', '.join(m.path for m in notes[:4])})" if notes else ""))

    # ── part 4: file output ───────────────────────────────────────────────
    if has[4] and last >= 4:
        seen = run_turn(agent, session_id, "Write the postmortem PDF for everything we found.")
        files = []
        for _ in range(6):  # outputs are indexed a few seconds after the turn ends
            files = agent.download_outputs(session_id)
            if files:
                break
            time.sleep(2)
        pdfs = [(name, data) for name, data in files if name.lower().endswith(".pdf") and data[:4] == b"%PDF"]
        saved = ""
        if pdfs:
            out_dir = HERE / "outputs"
            out_dir.mkdir(exist_ok=True)
            name = safe_filename(pdfs[0][0]) or "postmortem.pdf"
            (out_dir / name).write_bytes(pdfs[0][1])
            saved = f", saved outputs/{name} ({len(pdfs[0][1]) // 1024} KB)"
        verdict("part 4 · file output", bool(pdfs), f"{seen['seconds']}s, {len(files)} files, {len(pdfs)} PDFs{saved}")

    print()
    for name, ok in results.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    return 0 if results and all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
