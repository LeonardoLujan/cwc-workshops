# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0
import json
from pathlib import Path

import streamlit as st

from ui import INCIDENTS, inject_style, alert_banner
from provided import chat_panel

st.set_page_config(page_title="On-call · 3 pages open", page_icon="▮", layout="wide")
inject_style()

# Generate the synthetic log fixture on first run (~0.5 s, deterministic).
# Once data/app.log exists this branch is dead — a single stat() call —
# so reruns and code edits during the workshop don't regenerate it.
LOG_FIXTURE = Path(__file__).parent / "data" / "app.log"
if not LOG_FIXTURE.exists():
    with st.spinner("First run — generating `data/app.log` fixture…"):
        try:
            from data.generate_log import main as _generate_log
            _generate_log()
        except Exception as exc:  # disk full, permissions, etc.
            st.error(
                f"Couldn't generate `data/app.log` automatically (`{exc}`).\n\n"
                "Run it yourself, then reload:\n```\npython data/generate_log.py\n```"
            )
            st.stop()


def overview():
    main, side = st.columns([3, 5], gap="large")

    with main:
        st.markdown("### ON-CALL · 3 PAGES OPEN")
        alert_banner()

        DATA = Path(__file__).parent / "data"
        metrics = json.loads((DATA / "metrics.json").read_text())

        def peak(service: str, metric: str) -> float:
            return max(d["value"] for d in metrics[service][metric])

        def base(service: str, metric: str) -> float:
            return metrics[service][metric][0]["value"]

        r1c1, r1c2 = st.columns(2)
        r1c1.metric("CHECKOUT P99", f"{peak('checkout', 'p99_latency_ms'):,.0f} ms",
                    f"{(peak('checkout', 'p99_latency_ms') / base('checkout', 'p99_latency_ms') - 1) * 100:+,.0f}%",
                    delta_color="inverse")
        r1c2.metric("INVENTORY P99", f"{peak('inventory', 'p99_latency_ms'):,.0f} ms",
                    f"{(peak('inventory', 'p99_latency_ms') / base('inventory', 'p99_latency_ms') - 1) * 100:+,.0f}%",
                    delta_color="inverse")
        r2c1, r2c2 = st.columns(2)
        r2c1.metric("CART ERROR RATE", f"{peak('cart', 'error_rate') * 100:.1f}%",
                    f"+{peak('cart', 'error_rate') * 100:.1f} pp", delta_color="inverse")
        r2c2.metric("DB-PRIMARY CPU", f"{peak('db-primary', 'cpu_utilization') * 100:.0f}%",
                    f"+{(peak('db-primary', 'cpu_utilization') - base('db-primary', 'cpu_utilization')) * 100:.0f} pp",
                    delta_color="inverse")

        paged = {inc["service"]: inc["severity"] for inc in INCIDENTS}
        st.markdown(
            " &nbsp; ".join(
                f'<span class="pill {"crit" if paged.get(svc) == "critical" else "warn" if svc in paged else "ok"}">{svc}</span>'
                for svc in ["checkout", "cart", "auth", "inventory"]
            ),
            unsafe_allow_html=True,
        )

    with side:
        chat_panel(wide=True)


st.navigation([
    st.Page(overview, title="Overview", default=True),
    st.Page("pages/1_Metrics.py", title="Metrics"),
    st.Page("pages/2_Logs.py", title="Logs"),
    st.Page("pages/3_Deploys.py", title="Deploys"),
    st.Page("pages/4_Memory.py", title="Memory"),
]).run()
