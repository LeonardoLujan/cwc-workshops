# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0
"""
Everything pre-supplied for the workshop: model choices, prompts, tool
schemas, the local data those tools read, and the side-chat UI. You don't
edit this file — agent.py imports from it.

The things worth changing live elsewhere (ask Claude Code to change them):
  prompts/        what the commander and the investigators are told
  skills/         the runbook and the postmortem builder
  memory-seed/    what the team already knows
"""
import importlib
import inspect
import json
import os
import re
import time
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

import state

HERE = Path(__file__).parent
load_dotenv(HERE / ".env")

DATA = HERE / "data"
SKILLS = HERE / "skills"
MEMORY_SEED = HERE / "memory-seed"
PROMPTS = HERE / "prompts"

MANAGED_AGENTS_BETA = "managed-agents-2026-04-01"

# Each agent has its own model. Both default to Opus 5.5; set
# SRE_INVESTIGATOR_MODEL=claude-haiku-5-5 in .env for faster, cheaper threads.
COMMANDER_MODEL = os.environ.get("SRE_COMMANDER_MODEL", "claude-opus-5-5")
INVESTIGATOR_MODEL = os.environ.get("SRE_INVESTIGATOR_MODEL", "claude-opus-5-5")

COMMANDER_SYSTEM = (PROMPTS / "commander.md").read_text()
INVESTIGATOR_SYSTEM = (PROMPTS / "investigator.md").read_text()

TOOLS = [
    {"type": "agent_toolset_20260401", "default_config": {"enabled": True}},
    {"type": "custom", "name": "list_incidents",
     "description": "Pages that are open right now: id, service, signal, time fired.",
     "input_schema": {"type": "object", "properties": {}}},
    {"type": "custom", "name": "get_metrics",
     "description": "Per-minute timeseries for one service and metric over the incident hour. "
                    "An unknown name returns the list of valid services and metrics.",
     "input_schema": {"type": "object",
                      "properties": {"service": {"type": "string"}, "metric": {"type": "string"}},
                      "required": ["service", "metric"]}},
    {"type": "custom", "name": "get_recent_deploys", "description": "Deploys in the last 6h, all services.",
     "input_schema": {"type": "object", "properties": {}}},
    {"type": "custom", "name": "get_diff", "description": "Unified diff for a commit SHA.",
     "input_schema": {"type": "object", "properties": {"commit": {"type": "string"}},
                      "required": ["commit"]}},
]

incidents = json.loads((DATA / "incidents.json").read_text())
metrics = json.loads((DATA / "metrics.json").read_text())
deploys = (DATA / "deploys.json").read_text()
diffs = {path.stem: path.read_text() for path in sorted((DATA / "diffs").glob("*.diff"))}

# ── session outputs ───────────────────────────────────────────────────────
OUTPUT_TYPES = (".pdf", ".png")
MAX_OUTPUT_BYTES = 20 * 1024 * 1024


def safe_filename(name: str) -> str:
    """Filenames come from the agent's container: keep a plain basename only."""
    base = re.split(r"[\\/]", name or "")[-1]
    base = re.sub(r"[^A-Za-z0-9._-]", "_", base)
    return "" if base in ("", ".", "..") else base


def wanted_output(file) -> bool:
    """True for files worth offering as a download (type and size checked)."""
    name = safe_filename(getattr(file, "filename", ""))
    size = getattr(file, "size_bytes", 0) or 0
    return bool(name) and name.lower().endswith(OUTPUT_TYPES) and size <= MAX_OUTPUT_BYTES


def load_agent():
    """Your agent.py. Set SRE_AGENT_MODULE=solutions.agent to run the reference."""
    return importlib.import_module(os.environ.get("SRE_AGENT_MODULE", "agent"))


def implemented(fn) -> bool:
    """A stub is a function whose body still raises NotImplementedError."""
    try:
        return "raise NotImplementedError" not in inspect.getsource(fn)
    except (OSError, TypeError):
        return True


# ── side-chat UI ──────────────────────────────────────────────────────────
def _offline(fn: str):
    st.caption(f"agent offline — implement `{fn}()` in `agent.py`")
    st.chat_input("ask…", disabled=True, key=f"off_{fn}")


def _text(content) -> str:
    if not content:
        return ""
    return "".join(getattr(b, "text", "") for b in content if getattr(b, "type", None) == "text")


def _short(text: str, limit: int = 90) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _incident_in(text: str) -> str:
    match = re.search(r"INC-\d+", text or "")
    return match.group(0) if match else ""


@st.cache_data(ttl=20)
def _list_sessions(agent_id: str):
    page = load_agent().client.beta.sessions.list(agent_id=agent_id, limit=15, order="desc")
    items = sorted(page.data, key=lambda s: s.created_at, reverse=True)
    return [(s.id, f"{s.created_at:%H:%M:%S} · {s.status} · {s.id[-6:]}") for s in items]


def _load_history(session_id: str):
    """Replay a session's conversation from the server-side event log."""
    hist: list[tuple[str, str]] = []

    def say(line: str):
        if hist and hist[-1][0] == "assistant":
            hist[-1] = ("assistant", hist[-1][1] + line)
        else:
            hist.append(("assistant", line.lstrip()))

    events = load_agent().client.beta.sessions.events.list(session_id, order="asc", limit=500)
    for ev in events.data:
        if ev.type == "user.message":
            hist.append(("user", _text(ev.content)))
        elif ev.type == "agent.message":
            say("\n\n" + _text(ev.content))
        elif ev.type in ("agent.tool_use", "agent.custom_tool_use"):
            scope = "sandbox" if ev.type == "agent.tool_use" else "local"
            where = "thread · " if getattr(ev, "session_thread_id", None) else ""
            say(f"\n\n`{where}{scope} · {ev.name}`")
        elif ev.type == "session.thread_created":
            say(f"\n\n`thread · {ev.agent_name} started`")
        elif ev.type == "agent.thread_message_received":
            say(f"\n\n`thread · {getattr(ev, 'from_agent_name', None) or 'agent'} reported`")
    return hist


def _thread_rows(session_id: str) -> list[dict]:
    """One row per thread in the session: who ran, on which model, for how long."""
    rows = []
    try:
        threads = load_agent().client.beta.sessions.threads.list(session_id)
    except Exception:
        return rows
    for thread in threads:
        snapshot = getattr(thread, "agent", None)
        model = getattr(snapshot, "model", "")
        usage, stats = getattr(thread, "usage", None), getattr(thread, "stats", None)
        rows.append({
            "thread": "primary" if not getattr(thread, "parent_thread_id", None) else thread.id[-6:],
            "agent": getattr(snapshot, "name", "") or "",
            "model": getattr(model, "id", model) or "",
            "status": getattr(thread, "status", ""),
            "active s": round(getattr(stats, "active_seconds", 0) or 0),
            "out tokens": getattr(usage, "output_tokens", 0) or 0,
        })
    return rows


def _thread_activity(session_id: str, thread_id: str) -> str:
    """What a finished sub-agent did, read back from its own event log."""
    try:
        events = load_agent().client.beta.sessions.threads.events.list(thread_id, session_id=session_id, limit=200)
    except Exception:
        return ""
    sandbox = sum(1 for e in events.data if e.type == "agent.tool_use")
    local = sum(1 for e in events.data if e.type == "agent.custom_tool_use")
    return f"{sandbox} sandbox calls · {local} local tool calls"


def _fetch_outputs(agent, session_id: str, expect: bool = False) -> list[tuple[str, bytes]]:
    """Files the agent wrote to /mnt/session/outputs/. Indexing lags a few seconds."""
    if not implemented(agent.download_outputs):
        return []
    for attempt in range(5 if expect else 1):
        files = [(safe_filename(name), data) for name, data in agent.download_outputs(session_id)]
        files = [(name, data) for name, data in files if name]
        if files or not expect:
            return files
        time.sleep(2)
    return []


def _render_outputs(slot, session_id: str):
    files = st.session_state.get("outputs", {}).get(session_id, [])
    with slot:
        for name, data in files:
            mime = "application/pdf" if name.lower().endswith(".pdf") else "application/octet-stream"
            st.download_button(f"{name} · {max(1, len(data) // 1024)} KB", data, file_name=name, mime=mime,
                               icon=":material/download:", key=f"dl_{session_id}_{name}",
                               use_container_width=True)


def _render_threads(slot, session_id: str):
    rows = st.session_state.get("threads", {}).get(session_id, [])
    if len(rows) > 1:
        with slot.expander(f"threads · {len(rows)}"):
            st.dataframe(rows, hide_index=True, use_container_width=True)


def _stream_turn(agent, session_id: str, prompt: str) -> tuple[str, bool]:
    """Render one agent turn as it streams. Returns (text, wrote_outputs)."""
    text_ph = st.empty()
    buf, parts = "", []
    tool_boxes: dict[str, object] = {}
    lanes: dict[str, dict] = {}
    wrote_outputs = False

    def lane_for(thread_id, agent_name="agent"):
        if thread_id not in lanes:
            box = st.status(f"thread · {agent_name}", state="running")
            lanes[thread_id] = {"box": box, "name": agent_name, "incident": ""}
        return lanes[thread_id]

    def relabel(lane, state_name):
        tag = f"{lane['incident']} · " if lane["incident"] else ""
        lane["box"].update(label=f"thread · {tag}{lane['name']}", state=state_name)

    for ev in agent.stream_reply(session_id, prompt):
        kind = ev.type
        if kind == "agent.message":
            buf += _text(ev.content)
            text_ph.markdown(buf)
        elif kind in ("agent.tool_use", "agent.custom_tool_use"):
            scope = "sandbox" if kind == "agent.tool_use" else "local"
            args = json.dumps(ev.input)
            wrote_outputs = wrote_outputs or "/mnt/session/outputs" in args
            thread_id = getattr(ev, "session_thread_id", None)
            if thread_id:  # a sub-agent's call, cross-posted to the session stream
                lane_for(thread_id)["box"].markdown(f"`{scope} · {ev.name}` {_short(args) if args != '{}' else ''}")
            else:
                box = st.status(f"{scope} · {ev.name}", state="running")
                box.caption("args")
                box.code(args if args != "{}" else "(none)", language="json")
                tool_boxes[ev.id] = box
        elif kind in ("agent.tool_result", "user.custom_tool_result"):
            ref = getattr(ev, "tool_use_id", None) or getattr(ev, "custom_tool_use_id", None)
            box = tool_boxes.pop(ref, None)
            if box:
                box.caption("result")
                box.code(_text(ev.content)[:1500] or "(empty)", language="text")
                box.update(state="complete")
        elif kind == "session.thread_created":
            lane_for(ev.session_thread_id, ev.agent_name)
        elif kind == "agent.thread_message_sent":
            lane = lane_for(ev.to_session_thread_id, getattr(ev, "to_agent_name", None) or "agent")
            brief = _text(ev.content)
            lane["incident"] = lane["incident"] or _incident_in(brief)
            relabel(lane, "running")
            lane["box"].caption("brief")
            lane["box"].markdown(_short(brief, 400))
        elif kind == "agent.thread_message_received":
            lane = lane_for(ev.from_session_thread_id, getattr(ev, "from_agent_name", None) or "agent")
            report = _text(ev.content)
            lane["incident"] = lane["incident"] or _incident_in(report)
            lane["box"].caption("report")
            lane["box"].markdown(report[:2500])
            activity = _thread_activity(session_id, ev.from_session_thread_id)
            if activity:
                lane["box"].caption(activity)
            relabel(lane, "complete")
        elif kind == "session.thread_status_running" and ev.session_thread_id in lanes:
            relabel(lanes[ev.session_thread_id], "running")
        elif kind == "span.model_request_start":
            if buf:
                parts.append(buf)
            text_ph, buf = st.empty(), ""
        elif kind == "session.error":
            st.warning(getattr(getattr(ev, "error", None), "message", "session error"))
        elif kind == "session.status_terminated":
            st.warning("session terminated")
            break
        elif kind == "session.status_idle" and ev.stop_reason.type != "requires_action":
            if ev.stop_reason.type != "end_turn":
                st.warning(f"stopped: {ev.stop_reason.type}")
            break

    for box in tool_boxes.values():
        box.update(state="complete")
    for lane in lanes.values():
        relabel(lane, "complete")
    if buf:
        parts.append(buf)
    done = [f"`thread · {lane['incident'] or lane['name']} done`" for lane in lanes.values()]
    return "\n\n".join(done + parts), wrote_outputs


def chat_panel():
    import anthropic

    agent = load_agent()
    st.markdown("##### SRE AGENT")

    ids = {}
    for label, fn in [("env", "setup_environment"), ("log file", "upload_log"), ("agent", "setup_agent")]:
        try:
            ids[fn] = getattr(agent, fn)()
        except NotImplementedError:
            return _offline(fn)
        except anthropic.APIError as exc:
            st.error(f"`{fn}()` failed: {getattr(exc, 'message', exc)}")
            return
        st.caption(f"{label} · `{ids[fn]}`")
    env_id, log_id, agent_id = ids["setup_environment"], ids["upload_log"], ids["setup_agent"]

    parts = [("multiagent", agent.setup_investigator), ("memory stores", agent.setup_memory),
             ("file output", agent.download_outputs)]
    st.markdown(" &nbsp; ".join(
        f'<span class="pill {"ok" if implemented(fn) else "off"}">{label} {"on" if implemented(fn) else "off"}</span>'
        for label, fn in parts), unsafe_allow_html=True)
    st.caption(f"you are `{state.handle()}` · every resource you create carries that tag")

    # ── session picker: sessions are stateful + persisted server-side.
    # Never auto-create — resume the newest existing one, or show an empty state.
    listed = _list_sessions(agent_id)
    if "sid" not in st.session_state and listed:
        st.session_state.sid = listed[0][0]
        st.session_state.hist = _load_history(st.session_state.sid)
    sid = st.session_state.get("sid")
    st.session_state.setdefault("outputs", {})
    st.session_state.setdefault("threads", {})

    labels = dict(listed)
    ids_listed = [s for s, _ in listed]
    if sid and sid not in labels:
        ids_listed.insert(0, sid)
        labels[sid] = "just now · current"

    def _on_pick():
        chosen = st.session_state.session_picker
        st.session_state.sid = chosen
        st.session_state.hist = _load_history(chosen)

    if sid:
        st.session_state.session_picker = sid

    pick_col, new_col, del_col = st.columns([6, 1, 1])
    pick_col.selectbox(
        "session", ids_listed, format_func=lambda v: labels.get(v, v), disabled=not ids_listed,
        label_visibility="collapsed", key="session_picker", on_change=_on_pick,
    )
    if new_col.button("", icon=":material/add:", help="new session", use_container_width=True):
        try:
            st.session_state.sid = agent.start_session(agent_id, env_id, log_id)
        except NotImplementedError:
            st.toast("implement `start_session()` in `agent.py`")
        except anthropic.APIError as exc:
            st.error(f"`start_session()` failed: {getattr(exc, 'message', exc)}")
        else:
            st.session_state.hist = []
            _list_sessions.clear()
            st.rerun()
    if del_col.button("", icon=":material/delete:", help="delete session",
                      use_container_width=True, disabled=not sid):
        try:
            agent.delete_session(st.session_state.sid)
        except NotImplementedError:
            st.toast("implement `delete_session()` in `agent.py`")
        except anthropic.APIError as exc:
            st.error(f"`delete_session()` failed: {getattr(exc, 'message', exc)}")
        else:
            _list_sessions.clear()
            del st.session_state["sid"]
            st.rerun()

    if not sid:
        st.caption("no sessions — click **+** to start one")
        st.chat_input("ask…", disabled=True, key="off_nosession")
        return

    st.caption(f"`{sid}` — persisted in the cloud, not this browser")
    if sid not in st.session_state.outputs:
        try:
            st.session_state.outputs[sid] = _fetch_outputs(agent, sid)
        except anthropic.APIError:
            st.session_state.outputs[sid] = []
    if sid not in st.session_state.threads:
        st.session_state.threads[sid] = _thread_rows(sid)

    chat = st.container(height=400, border=False)
    with chat:
        for role, text in st.session_state.hist:
            with st.chat_message(role):
                st.markdown(text)
    extras = st.container()

    if q := st.chat_input("ask the agent…"):
        st.session_state.hist.append(("user", q))
        with chat:
            with st.chat_message("user"):
                st.markdown(q)
            with st.chat_message("assistant"):
                try:
                    reply, wrote_outputs = _stream_turn(agent, sid, q)
                except NotImplementedError:
                    st.warning("implement `stream_reply()` / `handle_tool()` in `agent.py`")
                    return
                except anthropic.APIError as exc:
                    st.error(f"`stream_reply()` failed: {getattr(exc, 'message', exc)}")
                    return
        st.session_state.hist.append(("assistant", reply))
        st.session_state.threads[sid] = _thread_rows(sid)
        try:
            st.session_state.outputs[sid] = _fetch_outputs(agent, sid, expect=wrote_outputs)
        except anthropic.APIError as exc:
            st.warning(f"`download_outputs()` failed: {getattr(exc, 'message', exc)}")

    _render_outputs(extras, sid)
    _render_threads(extras, sid)
