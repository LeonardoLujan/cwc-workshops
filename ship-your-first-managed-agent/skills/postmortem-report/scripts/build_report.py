#!/usr/bin/env python3
# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0
"""
Build the postmortem PDF from findings JSON files.

    python build_report.py --findings /workspace/findings --out /mnt/session/outputs/postmortem.pdf

Reads every <INCIDENT_ID>.json in --findings plus an optional summary.json.
Needs only matplotlib, which the cloud sandbox ships with.
"""
import argparse
import json
import sys
import textwrap
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.ticker  # noqa: E402
from matplotlib.backends.backend_pdf import PdfPages  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

PAGE_W, PAGE_H = 8.27, 11.69  # A4 portrait, inches
LEFT, RIGHT, TOP, BOTTOM = 0.08, 0.92, 0.93, 0.07
INK, DIM, RULE = "#111827", "#6b7280", "#d1d5db"
DARK, ACCENT, ALERT = "#0b0e14", "#39bae6", "#e5484d"
PALETTE = ["#e5484d", "#f59e0b", "#39bae6", "#7c3aed", "#10b981"]


def clean(value) -> str:
    """Plain text only: matplotlib treats $...$ as math."""
    return str(value if value is not None else "").replace("$", r"\$").strip()


class Report:
    """A tiny top-to-bottom layout on A4 pages."""

    def __init__(self, pdf: PdfPages):
        self.pdf = pdf
        self.pages = 0
        self.fig = None
        self.y = TOP
        self.new_page()

    def new_page(self) -> None:
        self.flush()
        self.fig = plt.figure(figsize=(PAGE_W, PAGE_H))
        self.y = TOP
        self.pages += 1

    def flush(self) -> None:
        if self.fig is not None:
            self.fig.text(RIGHT, 0.035, f"page {self.pages}", ha="right", fontsize=7, color=DIM)
            self.pdf.savefig(self.fig)
            plt.close(self.fig)
            self.fig = None

    @staticmethod
    def line_height(size: float) -> float:
        return size * 1.5 / (PAGE_H * 72)

    def need(self, height: float) -> None:
        if self.y - height < BOTTOM:
            self.new_page()

    def text(self, value, size=9.0, color=INK, weight="normal", indent=0.0, bullet="", gap=0.004) -> None:
        width_pt = (RIGHT - LEFT - indent) * PAGE_W * 72
        chars = max(20, int(width_pt / (size * (0.62 if weight == "bold" else 0.56))) - len(bullet))
        lines = textwrap.wrap(clean(value), chars) or [""]
        for i, line in enumerate(lines):
            self.need(self.line_height(size))
            prefix = bullet if i == 0 else " " * len(bullet)
            self.fig.text(LEFT + indent, self.y, prefix + line, fontsize=size, color=color,
                          weight=weight, va="top", family="DejaVu Sans")
            self.y -= self.line_height(size)
        self.y -= gap

    def heading(self, value, size=12.0, color=INK) -> None:
        self.need(self.line_height(size) * 3)
        self.y -= 0.008
        self.text(value, size=size, weight="bold", color=color, gap=0.002)
        self.rule()

    def rule(self, color=RULE) -> None:
        self.fig.add_artist(plt.Line2D([LEFT, RIGHT], [self.y, self.y], color=color, linewidth=0.6))
        self.y -= 0.010

    def space(self, amount=0.012) -> None:
        self.y -= amount

    def table(self, header: list[str], rows: list[list[str]], widths: list[float], size=8.5) -> None:
        """Columns are fractions of the text width; cells wrap."""
        total = RIGHT - LEFT
        lefts = [LEFT + total * sum(widths[:i]) for i in range(len(widths))]

        def draw_row(cells, weight="normal", color=INK):
            wrapped = []
            for cell, frac in zip(cells, widths):
                chars = max(6, int(total * frac * PAGE_W * 72 / (size * 0.56)) - 1)
                wrapped.append(textwrap.wrap(clean(cell), chars) or [""])
            height = max(len(w) for w in wrapped) * self.line_height(size)
            self.need(height + 0.008)
            for lines, x in zip(wrapped, lefts):
                for i, line in enumerate(lines):
                    self.fig.text(x, self.y - i * self.line_height(size), line, fontsize=size,
                                  color=color, weight=weight, va="top", family="DejaVu Sans")
            self.y -= height + 0.004
            self.fig.add_artist(plt.Line2D([LEFT, RIGHT], [self.y, self.y], color=RULE, linewidth=0.4))
            self.y -= 0.006

        draw_row(header, weight="bold", color=DIM)
        for row in rows:
            draw_row(row)
        self.space(0.006)

    def chart(self, title: str, height: float, label_room: float = 0.10):
        """A heading plus a chart area of `height` (figure fraction), kept on one page."""
        self.need(height + 0.11)
        self.heading(title)
        ax = self.fig.add_axes([LEFT + label_room, self.y - height, RIGHT - LEFT - label_room - 0.02, height])
        self.y -= height + 0.05
        return ax


def seconds(clock: str) -> float | None:
    """'14:31:18' or '14:31' (optionally with a date prefix) -> seconds since midnight."""
    try:
        parts = [float(p) for p in clean(clock).rstrip("Z").split("T")[-1].split(":")]
        return parts[0] * 3600 + parts[1] * 60 + (parts[2] if len(parts) > 2 else 0)
    except (ValueError, IndexError):
        return None


def load(findings_dir: Path) -> tuple[list[dict], dict, list[str]]:
    problems, incidents, summary = [], [], {}
    for path in sorted(findings_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            problems.append(f"{path.name}: not valid JSON ({exc})")
            continue
        if path.name == "summary.json":
            summary = data
            continue
        missing = [k for k in ("incident_id", "service", "root_cause") if not data.get(k)]
        if missing:
            problems.append(f"{path.name}: missing {', '.join(missing)}")
            continue
        if isinstance(data["root_cause"], str):
            data["root_cause"] = {"ref": "", "summary": data["root_cause"]}
        incidents.append(data)
    if not incidents:
        problems.append(f"no incident findings found in {findings_dir}")
    return incidents, summary, problems


def cover(r: Report, incidents: list[dict], summary: dict) -> None:
    causes = summary.get("root_causes") or []
    n_causes = len(causes) or len({clean(i["root_cause"].get("ref")) for i in incidents})
    r.fig.add_artist(Rectangle((0, 0.885), 1, 0.115, color=DARK, transform=r.fig.transFigure))
    r.fig.add_artist(Rectangle((0, 0.881), 1, 0.004, color=ACCENT, transform=r.fig.transFigure))
    r.fig.text(LEFT, 0.965, "POSTMORTEM", fontsize=9, color=ACCENT, weight="bold", va="top")
    title = textwrap.shorten(clean(summary.get("title") or "Incident report"), 90, placeholder="…")
    size = max(10.5, min(17.0, 17.0 * 44 / max(len(title), 1)))  # shrink long titles to fit the page width
    r.fig.text(LEFT, 0.945, title, fontsize=size, color="white", weight="bold", va="top")
    r.fig.text(LEFT, 0.905, f"{len(incidents)} pages  ·  {n_causes} root causes  ·  times in UTC",
               fontsize=9, color="#c9d1d9", va="top")
    r.y = 0.86

    if summary.get("summary"):
        r.text(summary["summary"], size=10, gap=0.012)

    r.heading("Pages and root causes")
    rows = []
    for inc in incidents:
        cause = inc["root_cause"]
        ref = clean(cause.get("ref"))
        if inc.get("caused_by_incident"):
            ref = f"{ref} (symptom of {clean(inc['caused_by_incident'])})" if ref else f"symptom of {clean(inc['caused_by_incident'])}"
        rows.append([inc["incident_id"], inc["service"], ref, cause.get("summary", "")])
    r.table(["Page", "Service", "Root cause", "Mechanism"], rows, [0.13, 0.12, 0.25, 0.50])

    impact_chart(r, incidents)
    timeline_chart(r, incidents)


def impact_chart(r: Report, incidents: list[dict]) -> None:
    bars = []
    for inc in incidents:
        impact = inc.get("impact") or {}
        try:
            base, peak = float(impact["baseline"]), float(impact["peak"])
        except (KeyError, TypeError, ValueError):
            continue
        if base > 0 and peak > 0:
            label = f"{inc['incident_id']}  {clean(impact.get('metric'))}"
            detail = f"{base:g} → {peak:g} {clean(impact.get('unit'))}".strip()
            bars.append((label, peak / base, detail))
    if not bars:
        return
    ax = r.chart("Impact: peak against baseline", 0.035 * len(bars) + 0.03, label_room=0.21)
    names = [b[0] for b in bars][::-1]
    ratios = [b[1] for b in bars][::-1]
    ax.barh(names, ratios, color=ALERT, height=0.55)
    ax.set_xscale("log")
    ax.set_xlim(1, max(ratios) * 6)
    for i, (_, ratio, detail) in enumerate(bars[::-1]):
        ax.text(ratio * 1.12, i, f"{ratio:,.0f}×   ({detail})", va="center", fontsize=7.5, color=INK)
    ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:g}×"))
    ax.tick_params(labelsize=7.5, length=0)
    ax.set_xlabel("multiple of baseline (log scale)", fontsize=7.5, color=DIM)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)


def timeline_chart(r: Report, incidents: list[dict]) -> None:
    lanes = []
    for inc in incidents:
        points = [(seconds(e.get("time")), clean(e.get("event"))) for e in inc.get("timeline") or []]
        points = [(t, e) for t, e in points if t is not None]
        if points:
            lanes.append((inc["incident_id"], sorted(points)))
    if not lanes:
        return
    ax = r.chart("Timeline", 0.05 * len(lanes) + 0.03)
    for row, (name, points) in enumerate(lanes[::-1]):
        color = PALETTE[(len(lanes) - 1 - row) % len(PALETTE)]
        times = [t for t, _ in points]
        ax.hlines(row, min(times), max(times), color=color, linewidth=2, alpha=0.35)
        ax.scatter(times, [row] * len(times), color=color, s=26, zorder=3)
        ax.annotate(textwrap.shorten(points[0][1], 46, placeholder="…"), (times[0], row),
                    xytext=(0, 7), textcoords="offset points", fontsize=6.5, color=INK)
    ax.set_yticks(range(len(lanes)))
    ax.set_yticklabels([name for name, _ in lanes[::-1]], fontsize=7.5)
    ax.set_ylim(-0.6, len(lanes) - 0.3)
    lo = min(t for _, pts in lanes for t, _ in pts)
    hi = max(t for _, pts in lanes for t, _ in pts)
    pad = max(60.0, (hi - lo) * 0.06)
    ax.set_xlim(lo - pad, hi + pad * 3)
    ticks = ax.get_xticks()
    ax.set_xticks(ticks)
    ax.set_xticklabels([f"{int(t // 3600):02d}:{int(t % 3600 // 60):02d}" for t in ticks], fontsize=7.5)
    ax.tick_params(length=0)
    ax.grid(axis="x", color=RULE, linewidth=0.4)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)


def incident_section(r: Report, inc: dict) -> None:
    cause = inc["root_cause"]
    r.heading(f"{inc['incident_id']}  ·  {inc['service']}", size=13, color=DARK)
    if inc.get("title"):
        r.text(inc["title"], size=10, color=DIM, gap=0.008)
    ref = clean(cause.get("ref"))
    r.text(f"Root cause: {ref + ' — ' if ref else ''}{clean(cause.get('summary'))}", size=10, weight="bold", gap=0.008)
    if inc.get("caused_by_incident"):
        r.text(f"This page is a symptom of {inc['caused_by_incident']}.", size=9.5, color=ALERT, gap=0.008)

    timeline = [[e.get("time", ""), e.get("event", "")] for e in inc.get("timeline") or []]
    if timeline:
        r.need(0.07)  # keep the label with at least the first rows
        r.text("Timeline", size=9, weight="bold", color=DIM)
        r.table(["Time", "Event"], timeline, [0.14, 0.86])
    for label, key in (("Evidence", "evidence"), ("Ruled out", "ruled_out")):
        items = inc.get(key) or []
        if items:
            r.text(label, size=9, weight="bold", color=DIM)
            for item in items:
                r.text(item, bullet="•  ", indent=0.01)
            r.space(0.006)
    if inc.get("fix"):
        r.text("Fix", size=9, weight="bold", color=DIM)
        r.text(inc["fix"], gap=0.012)


def build(findings_dir: Path, out: Path) -> int:
    incidents, summary, problems = load(findings_dir)
    if problems:
        print("cannot build the report:\n  " + "\n  ".join(problems), file=sys.stderr)
        return 1
    out.parent.mkdir(parents=True, exist_ok=True)
    with PdfPages(out) as pdf:
        report = Report(pdf)
        cover(report, incidents, summary)
        for inc in incidents:
            incident_section(report, inc)
            report.space(0.02)
        actions = summary.get("actions") or []
        if actions:
            report.heading("Action items")
            rows = [[a.get("priority", ""), a.get("owner", ""), a.get("action", "")] for a in actions]
            report.table(["Priority", "Owner", "Action"], rows, [0.14, 0.18, 0.68])
        report.flush()
        pages = report.pages
    print(f"wrote {out} ({pages} pages, {len(incidents)} incidents, {out.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--findings", type=Path, default=Path("/workspace/findings"))
    parser.add_argument("--out", type=Path, default=Path("/mnt/session/outputs/postmortem.pdf"))
    args = parser.parse_args()
    sys.exit(build(args.findings, args.out))
