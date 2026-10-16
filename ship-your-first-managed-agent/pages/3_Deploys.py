# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0
import json
from pathlib import Path

import streamlit as st

from ui import alert_banner
from provided import chat_panel

main, side = st.columns([5, 3], gap="large")
with side:
    chat_panel()

main.title("DEPLOYS")
with main:
    alert_banner()

DATA = Path(__file__).parent.parent / "data"
deploys = json.loads((DATA / "deploys.json").read_text())
diffs = {path.stem: path.read_text() for path in (DATA / "diffs").glob("*.diff")}

main.caption("last 6h · all services")

for d in sorted(deploys, key=lambda x: x["ts"], reverse=True):
    main.markdown(
        "<div class='deploy-row'>"
        f"<span class='ts'>{d['ts']}</span> · "
        f"<b>{d['service']}</b> · "
        f"<span class='sha'>{d['commit']}</span> · "
        f"{d['author']} · "
        f"{d['summary']}"
        "</div>",
        unsafe_allow_html=True,
    )

main.divider()
sel = main.selectbox("inspect commit", [d["commit"] for d in deploys])
if sel in diffs:
    main.code(diffs[sel], language="diff")
else:
    main.caption("diff not available for this commit")
