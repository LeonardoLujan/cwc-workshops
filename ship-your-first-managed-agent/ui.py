# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0
"""Tiny shared UI helpers — load CSS and render the open pages."""
import json
from html import escape
from pathlib import Path

import streamlit as st

_ROOT = Path(__file__).parent
INCIDENTS = json.loads((_ROOT / "data" / "incidents.json").read_text())


def inject_style():
    css = (_ROOT / "assets" / "style.css").read_text()
    st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)


def alert_banner(compact: bool = True):
    """One banner per open page. `compact` is the slim strip used on sub-pages."""
    cls = "pager-alert compact" if compact else "pager-alert"
    rows = "".join(
        f"""
<div class="{cls} {escape(inc['severity'])}">
  <span class="badge">{escape(inc['severity'].upper())}</span>
  <span class="title">{escape(inc['id'])} · {escape(inc['title'])}</span>
  <div class="meta">triggered {escape(inc['fired_at'].replace('T', ' ').replace('Z', ' UTC'))} · {escape(inc['signal'])} · ack: —</div>
</div>"""
        for inc in INCIDENTS
    )
    st.markdown(rows, unsafe_allow_html=True)


PLOTLY_LAYOUT = dict(
    template="plotly_dark",
    paper_bgcolor="#0b0e14",
    plot_bgcolor="#11151c",
    font=dict(family="IBM Plex Mono, monospace", color="#c9d1d9", size=11),
    margin=dict(l=40, r=20, t=30, b=40),
    xaxis=dict(gridcolor="#1e2530", zeroline=False),
    yaxis=dict(gridcolor="#1e2530", zeroline=False),
    hoverlabel=dict(font_family="IBM Plex Mono"),
)
