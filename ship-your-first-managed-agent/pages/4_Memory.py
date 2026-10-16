# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0
"""What the agent remembers between sessions: the two memory stores from Part 3."""
import streamlit as st

import state
from provided import chat_panel, implemented, load_agent
from ui import alert_banner

main, side = st.columns([5, 3], gap="large")
with side:
    chat_panel()

main.title("MEMORY")
with main:
    alert_banner()

agent = load_agent()

STORES = [
    ("memory:team-history", "TEAM HISTORY", "read_only", "Seeded from `memory-seed/`. The agent can read it, not change it."),
    ("memory:notebook", "AGENT NOTEBOOK", "read_write", "Starts empty. The agent writes here when an investigation ends."),
]


@st.cache_data(ttl=15)
def notes(store_id: str) -> list[dict]:
    """Every memory in a store, with its content."""
    found = []
    for item in agent.client.beta.memory_stores.memories.list(store_id, path_prefix="/", view="full"):
        if getattr(item, "type", "") == "memory":
            found.append({"path": item.path, "bytes": item.content_size_bytes,
                          "updated": f"{item.updated_at:%H:%M:%S}", "content": item.content or ""})
    return sorted(found, key=lambda n: n["path"])


@st.cache_data(ttl=15)
def history(store_id: str) -> list[dict]:
    """Every change to the store: who wrote what, and when."""
    rows = []
    for v in agent.client.beta.memory_stores.memory_versions.list(store_id, limit=30):
        author = getattr(v, "created_by", None)
        rows.append({"when": f"{v.created_at:%H:%M:%S}", "operation": v.operation, "path": v.path or "",
                     "by": getattr(author, "type", "") or "", "session": (getattr(author, "session_id", "") or "")[-6:]})
    return rows


with main:
    if not implemented(agent.setup_memory):
        st.caption("memory stores off — implement `setup_memory()` in `agent.py` (Part 3)")
    elif not state.get("memory:notebook"):
        st.caption("no stores yet — start a session with **+** and they are created")
    else:
        if st.button("refresh", icon=":material/refresh:"):
            notes.clear()
            history.clear()
        for key, title, access, blurb in STORES:
            store_id = state.get(key)
            st.markdown(f"##### {title}")
            st.caption(f"`{store_id}` · `{access}` · {blurb}")
            try:
                found = notes(store_id)
            except Exception as exc:  # the store was removed, or the key changed
                st.warning(f"could not read this store: {exc}")
                continue
            if not found:
                st.caption("empty")
            for note in found:
                with st.expander(f"{note['path']} · {note['bytes']} bytes · updated {note['updated']}"):
                    st.markdown(note["content"])
            if access == "read_write" and found:
                with st.expander("version history"):
                    st.dataframe(history(store_id), hide_index=True, use_container_width=True)
