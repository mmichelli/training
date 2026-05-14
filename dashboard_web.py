"""FastAPI + HTMX training dashboard — expedition logbook aesthetic.

Run:
    uv run uvicorn dashboard_web:app --reload --port 8765

Open http://localhost:8765.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from jinja2 import Template

import features as F
from plan_lookup import PLAN_START, WEEKLY_TOTAL, prescription_for

ROOT = Path(__file__).parent
DATA = ROOT / "data"

app = FastAPI()


# ─── Theme tokens ────────────────────────────────────────────────────────

PAPER = "#F1ECE0"        # warm cream
PAPER_DEEP = "#E8E1D2"   # slightly darker for inset cards
INK = "#1B1F2A"          # near-black blue
INK_SOFT = "#5A5E6B"
RULE = "#9C9484"
OXIDE = "#C8362D"        # signal red
FOREST = "#4A6B47"       # go green
OCHRE = "#C18B3D"        # caution amber
GRID = "rgba(28,31,42,0.06)"


# ─── Data loaders ────────────────────────────────────────────────────────

def load_hrv_summaries() -> pd.DataFrame:
    rows = []
    if not (DATA / "hrv").exists():
        return pd.DataFrame()
    for p in sorted((DATA / "hrv").glob("*.json")):
        try:
            obj = json.loads(p.read_text())
            s = obj.get("hrvSummary") or {}
            if not s.get("calendarDate"):
                continue
            baseline = s.get("baseline") or {}
            rows.append({
                "date": s["calendarDate"],
                "last_night_avg": s.get("lastNightAvg"),
                "last_night_5min_high": s.get("lastNight5MinHigh"),
                "weekly_avg": s.get("weeklyAvg"),
                "status": s.get("status"),
                "baseline_low": baseline.get("lowUpper"),
                "baseline_balanced_low": baseline.get("balancedLow"),
                "baseline_balanced_upper": baseline.get("balancedUpper"),
            })
        except Exception:
            continue
    df = pd.DataFrame(rows)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)
    return df


def load_sleep_summaries() -> pd.DataFrame:
    rows = []
    if not (DATA / "sleep").exists():
        return pd.DataFrame()
    for p in sorted((DATA / "sleep").glob("*.json")):
        try:
            obj = json.loads(p.read_text())
            d = obj.get("dailySleepDTO", obj)
            if not d.get("calendarDate"):
                continue
            total = d.get("sleepTimeSeconds") or 0
            rows.append({
                "date": d["calendarDate"],
                "total_h": total / 3600,
                "deep_h": (d.get("deepSleepSeconds") or 0) / 3600,
                "light_h": (d.get("lightSleepSeconds") or 0) / 3600,
                "rem_h": (d.get("remSleepSeconds") or 0) / 3600,
                "awake_h": (d.get("awakeSleepSeconds") or 0) / 3600,
                "avg_stress": d.get("avgSleepStress"),
                "avg_resp": d.get("averageRespirationValue"),
            })
        except Exception:
            continue
    df = pd.DataFrame(rows)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)
    return df


def load_daily_summaries() -> pd.DataFrame:
    rows = []
    if not (DATA / "daily").exists():
        return pd.DataFrame()
    for p in sorted((DATA / "daily").glob("*.json")):
        try:
            obj = json.loads(p.read_text())
            if not obj.get("calendarDate"):
                continue
            rows.append({
                "date": obj["calendarDate"],
                "rhr": obj.get("restingHeartRate"),
                "rhr_7d": obj.get("lastSevenDaysAvgRestingHeartRate"),
                "steps": obj.get("totalSteps"),
                "active_kcal": obj.get("activeKilocalories"),
                "vigorous_min": obj.get("vigorousIntensityMinutes"),
                "moderate_min": obj.get("moderateIntensityMinutes"),
            })
        except Exception:
            continue
    df = pd.DataFrame(rows)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
        df = df.dropna(subset=["rhr"]).sort_values("date").reset_index(drop=True)
    return df


def load_stress_summaries() -> pd.DataFrame:
    rows = []
    if not (DATA / "stress").exists():
        return pd.DataFrame()
    for p in sorted((DATA / "stress").glob("*.json")):
        try:
            obj = json.loads(p.read_text())
            rows.append({
                "date": obj.get("calendarDate"),
                "avg_stress": obj.get("avgStressLevel"),
                "max_stress": obj.get("maxStressLevel"),
            })
        except Exception:
            continue
    df = pd.DataFrame(rows)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
        df = df.dropna(subset=["avg_stress"]).sort_values("date").reset_index(drop=True)
    return df


def weekly_volume() -> pd.DataFrame:
    acts = F.load_activities()
    if acts.empty:
        return pd.DataFrame()
    acts["week"] = pd.to_datetime(acts["date"]).dt.to_period("W-MON").apply(lambda r: r.start_time.date())
    wk = acts.groupby("week")["duration_s"].sum().div(3600).rename("actual_h").to_frame()
    wk["plan_week"] = [((d - PLAN_START).days // 7) + 1 for d in wk.index]
    wk["target_h"] = wk["plan_week"].map(WEEKLY_TOTAL).fillna(0)
    return wk.reset_index()


# ─── Verdict ──────────────────────────────────────────────────────────────

def readiness_verdict() -> tuple[str, list[str]]:
    hrv = load_hrv_summaries()
    stress = load_stress_summaries()
    sleep = load_sleep_summaries()
    daily = load_daily_summaries()

    reasons: list[str] = []
    light = "green"

    def amber(r):
        nonlocal light
        reasons.append(r)
        if light == "green":
            light = "amber"

    def red(r):
        nonlocal light
        reasons.append(r)
        light = "red"

    if not hrv.empty:
        last = hrv.iloc[-1]
        status = last["status"]
        if status == "UNBALANCED":
            amber(f"HRV unbalanced · last night {int(last['last_night_avg'])} ms")
        elif status == "POOR":
            red(f"HRV poor · last night {int(last['last_night_avg'])} ms")
        if len(hrv) >= 7 and pd.notna(last["weekly_avg"]):
            week_ago = hrv.iloc[-7]["weekly_avg"]
            if pd.notna(week_ago):
                delta = last["weekly_avg"] - week_ago
                if delta < -3:
                    amber(f"7-day HRV trend {delta:+.0f} ms")

    if not stress.empty:
        last = stress.iloc[-1]
        if pd.notna(last["avg_stress"]) and last["avg_stress"] > 50:
            amber(f"avg stress {int(last['avg_stress'])}/100")

    if not sleep.empty:
        last = sleep.iloc[-1]
        if last["total_h"] < 6:
            red(f"only {last['total_h']:.1f}h sleep last night")
        elif last["total_h"] < 7:
            amber(f"{last['total_h']:.1f}h sleep last night")
        if len(sleep) >= 7:
            week_avg = sleep.tail(7)["total_h"].mean()
            if week_avg < 7:
                amber(f"7-day sleep avg {week_avg:.1f}h · target 7.5h")

    if not daily.empty and len(daily) >= 14:
        last = daily.iloc[-1]
        baseline = daily.iloc[-14:-1]["rhr"].mean()
        if pd.notna(last["rhr"]) and pd.notna(baseline):
            delta = last["rhr"] - baseline
            if delta > 5:
                red(f"RHR {int(last['rhr'])} · {delta:+.0f} vs baseline")
            elif delta > 3:
                amber(f"RHR {int(last['rhr'])} · {delta:+.0f} vs baseline")

    if not reasons:
        reasons.append("autonomic markers nominal · proceed as written")
    return light, reasons


# ─── Plotly: shared layout ───────────────────────────────────────────────

def chart_layout(title: str | None = None, height: int = 240) -> dict:
    return dict(
        height=height,
        margin=dict(l=44, r=16, t=14 if not title else 28, b=32),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="'IBM Plex Mono', monospace", color=INK, size=11),
        title=dict(text=title, font=dict(size=11, color=INK_SOFT)) if title else None,
        showlegend=False,
        xaxis=dict(
            gridcolor=GRID, linecolor=RULE, linewidth=1, ticks="outside",
            tickcolor=RULE, ticklen=4, tickfont=dict(size=10, color=INK_SOFT),
            showspikes=False,
        ),
        yaxis=dict(
            gridcolor=GRID, linecolor=RULE, linewidth=1, ticks="outside",
            tickcolor=RULE, ticklen=4, tickfont=dict(size=10, color=INK_SOFT),
            zeroline=False,
        ),
    )


def chart_html(fig: go.Figure, div_id: str) -> str:
    return fig.to_html(include_plotlyjs="cdn", div_id=div_id, full_html=False,
                       config={"displayModeBar": False, "responsive": True})


def hrv_chart() -> str:
    df = load_hrv_summaries().tail(60)
    if df.empty:
        return "<div class='empty'>no HRV data yet</div>"
    fig = go.Figure()
    # balanced range band — subtle
    fig.add_trace(go.Scatter(x=df["date"], y=df["baseline_balanced_upper"],
                            mode="lines", line=dict(width=0), hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=df["date"], y=df["baseline_balanced_low"],
                            mode="lines", line=dict(width=0),
                            fill="tonexty", fillcolor="rgba(74,107,71,0.10)",
                            hoverinfo="skip"))
    # weekly avg
    fig.add_trace(go.Scatter(x=df["date"], y=df["weekly_avg"], mode="lines",
                            line=dict(color=INK_SOFT, width=1.2, dash="dot")))
    # nightly
    fig.add_trace(go.Scatter(x=df["date"], y=df["last_night_avg"],
                            mode="lines+markers",
                            line=dict(color=INK, width=1.5),
                            marker=dict(size=4, color=INK, line=dict(color=PAPER, width=1))))
    fig.update_layout(**chart_layout())
    fig.update_yaxes(title=dict(text="rMSSD · ms", font=dict(size=10, color=INK_SOFT)))
    return chart_html(fig, "hrv-chart")


def rhr_chart() -> str:
    df = load_daily_summaries().tail(60)
    if df.empty:
        return "<div class='empty'>no daily summary data yet</div>"
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df["date"], y=df["rhr_7d"], mode="lines",
                            line=dict(color=INK_SOFT, width=1.2, dash="dot")))
    fig.add_trace(go.Scatter(x=df["date"], y=df["rhr"], mode="lines+markers",
                            line=dict(color=OXIDE, width=1.5),
                            marker=dict(size=4, color=OXIDE, line=dict(color=PAPER, width=1))))
    fig.update_layout(**chart_layout())
    fig.update_yaxes(title=dict(text="bpm", font=dict(size=10, color=INK_SOFT)))
    return chart_html(fig, "rhr-chart")


def sleep_chart() -> str:
    df = load_sleep_summaries().tail(30)
    if df.empty:
        return "<div class='empty'>no sleep data yet</div>"
    fig = go.Figure()
    fig.add_trace(go.Bar(x=df["date"], y=df["deep_h"], marker_color=INK, name="deep"))
    fig.add_trace(go.Bar(x=df["date"], y=df["rem_h"], marker_color="#3A4156", name="REM"))
    fig.add_trace(go.Bar(x=df["date"], y=df["light_h"], marker_color=RULE, name="light"))
    fig.add_trace(go.Bar(x=df["date"], y=df["awake_h"], marker_color="rgba(156,148,132,0.4)", name="awake"))
    fig.add_hline(y=7.5, line=dict(color=FOREST, width=1, dash="dash"))
    layout = chart_layout()
    layout.update(barmode="stack")
    fig.update_layout(**layout)
    fig.update_yaxes(title=dict(text="hours", font=dict(size=10, color=INK_SOFT)))
    return chart_html(fig, "sleep-chart")


def stress_chart() -> str:
    df = load_stress_summaries().tail(60)
    if df.empty:
        return "<div class='empty'>no stress data yet</div>"
    fig = go.Figure()
    fig.add_trace(go.Bar(x=df["date"], y=df["avg_stress"],
                        marker_color=OCHRE, marker_line=dict(width=0)))
    fig.add_trace(go.Scatter(x=df["date"], y=df["max_stress"], mode="lines",
                            line=dict(color=OXIDE, width=1, dash="dot")))
    fig.update_layout(**chart_layout())
    fig.update_yaxes(title=dict(text="stress · 0-100", font=dict(size=10, color=INK_SOFT)))
    return chart_html(fig, "stress-chart")


def volume_chart() -> str:
    df = weekly_volume().tail(12)
    if df.empty:
        return "<div class='empty'>no activities synced yet</div>"
    fig = go.Figure()
    fig.add_trace(go.Bar(x=df["week"], y=df["target_h"], marker_color="rgba(156,148,132,0.45)",
                        marker_line=dict(color=RULE, width=1)))
    fig.add_trace(go.Bar(x=df["week"], y=df["actual_h"], marker_color=INK, marker_line=dict(width=0)))
    layout = chart_layout(height=220)
    layout.update(barmode="overlay")
    fig.update_layout(**layout)
    fig.update_yaxes(title=dict(text="hours", font=dict(size=10, color=INK_SOFT)))
    return chart_html(fig, "volume-chart")


# ─── Templates ────────────────────────────────────────────────────────────

PAGE = Template(r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Two Oceans · log</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght@0,9..144,300..900;1,9..144,300..900&family=IBM+Plex+Mono:wght@300;400;500;600&family=IBM+Plex+Sans:wght@300;400;500;600&display=swap" rel="stylesheet">
  <script src="https://unpkg.com/htmx.org@2.0.4"></script>
  <script>
    document.addEventListener('htmx:afterSwap', (e) => {
      e.detail.target.querySelectorAll('script').forEach((old) => {
        const s = document.createElement('script');
        for (const a of old.attributes) s.setAttribute(a.name, a.value);
        s.textContent = old.textContent;
        old.replaceWith(s);
      });
    });
  </script>
  <style>
    :root {
      --paper: {{ PAPER }};
      --paper-deep: {{ PAPER_DEEP }};
      --ink: {{ INK }};
      --ink-soft: {{ INK_SOFT }};
      --rule: {{ RULE }};
      --oxide: {{ OXIDE }};
      --forest: {{ FOREST }};
      --ochre: {{ OCHRE }};
    }
    * { box-sizing: border-box; }
    html, body { margin: 0; padding: 0; }
    body {
      background: var(--paper);
      color: var(--ink);
      font-family: 'IBM Plex Sans', system-ui, sans-serif;
      font-weight: 400;
      font-size: 15px;
      line-height: 1.5;
      min-height: 100vh;
      position: relative;
      letter-spacing: 0.005em;
    }
    /* Topographic contour pattern */
    body::before {
      content: '';
      position: fixed; inset: 0;
      background-image:
        radial-gradient(ellipse 80% 60% at 18% 30%,
          transparent 35%, rgba(28,31,42,0.018) 35%, rgba(28,31,42,0.018) 36%, transparent 36%,
          transparent 45%, rgba(28,31,42,0.022) 45%, rgba(28,31,42,0.022) 46%, transparent 46%,
          transparent 55%, rgba(28,31,42,0.026) 55%, rgba(28,31,42,0.026) 56%, transparent 56%,
          transparent 65%, rgba(28,31,42,0.020) 65%, rgba(28,31,42,0.020) 66%, transparent 66%,
          transparent 75%, rgba(28,31,42,0.016) 75%, rgba(28,31,42,0.016) 76%, transparent 76%),
        radial-gradient(ellipse 60% 50% at 82% 70%,
          transparent 30%, rgba(28,31,42,0.020) 30%, rgba(28,31,42,0.020) 31%, transparent 31%,
          transparent 42%, rgba(28,31,42,0.024) 42%, rgba(28,31,42,0.024) 43%, transparent 43%,
          transparent 56%, rgba(28,31,42,0.028) 56%, rgba(28,31,42,0.028) 57%, transparent 57%,
          transparent 70%, rgba(28,31,42,0.020) 70%, rgba(28,31,42,0.020) 71%, transparent 71%);
      pointer-events: none;
      z-index: 0;
    }
    /* Subtle paper grain */
    body::after {
      content: '';
      position: fixed; inset: 0;
      background-image:
        repeating-linear-gradient(0deg, transparent 0 31px, rgba(28,31,42,0.015) 31px 32px),
        repeating-linear-gradient(90deg, transparent 0 31px, rgba(28,31,42,0.012) 31px 32px);
      pointer-events: none;
      z-index: 0;
      mix-blend-mode: multiply;
    }
    .shell {
      position: relative; z-index: 1;
      max-width: 1180px;
      margin: 0 auto;
      padding: 40px 48px 80px;
    }
    /* Masthead */
    header.masthead {
      display: grid;
      grid-template-columns: 1fr auto;
      align-items: end;
      padding-bottom: 18px;
      border-bottom: 1px solid var(--rule);
      margin-bottom: 32px;
    }
    .brand {
      font-family: 'Fraunces', serif;
      font-optical-sizing: auto;
      font-variation-settings: "opsz" 144, "SOFT" 100;
      font-weight: 600;
      font-size: clamp(40px, 6vw, 64px);
      line-height: 0.92;
      letter-spacing: -0.025em;
      color: var(--ink);
    }
    .brand em {
      font-style: italic;
      font-variation-settings: "opsz" 144, "SOFT" 100;
      color: var(--oxide);
    }
    .tagline {
      margin-top: 8px;
      font-family: 'IBM Plex Mono', monospace;
      font-size: 11px;
      letter-spacing: 0.18em;
      text-transform: uppercase;
      color: var(--ink-soft);
    }
    .stamp {
      text-align: right;
      font-family: 'IBM Plex Mono', monospace;
      font-size: 11px;
      letter-spacing: 0.08em;
      color: var(--ink-soft);
      line-height: 1.6;
    }
    .stamp .big {
      font-family: 'Fraunces', serif;
      font-style: italic;
      font-weight: 400;
      font-size: 22px;
      letter-spacing: -0.01em;
      color: var(--ink);
      text-transform: none;
      display: block;
      margin-bottom: 2px;
    }
    .refresh {
      background: none; border: none; cursor: pointer;
      font-family: 'IBM Plex Mono', monospace;
      font-size: 10px; letter-spacing: 0.15em;
      color: var(--ink-soft); text-transform: uppercase;
      padding: 4px 0; margin-top: 6px;
      border-bottom: 1px solid var(--rule);
    }
    .refresh:hover { color: var(--oxide); border-color: var(--oxide); }

    /* Card grid */
    .grid {
      display: grid;
      grid-template-columns: 1fr 1fr 1fr;
      gap: 36px 32px;
    }
    .grid > * { min-width: 0; }
    .span-2 { grid-column: span 2; }
    .span-3 { grid-column: span 3; }
    @media (max-width: 980px) {
      .grid { grid-template-columns: 1fr 1fr; }
      .span-2, .span-3 { grid-column: span 2; }
    }
    @media (max-width: 640px) {
      .shell { padding: 24px 20px 60px; }
      .grid { grid-template-columns: 1fr; }
      .span-2, .span-3 { grid-column: span 1; }
    }

    /* Section header */
    .section {
      position: relative;
      padding-top: 4px;
    }
    .section-head {
      display: flex; align-items: baseline; gap: 14px;
      margin-bottom: 14px;
      padding-bottom: 8px;
      border-bottom: 1px solid var(--rule);
    }
    .roman {
      font-family: 'Fraunces', serif;
      font-style: italic;
      font-variation-settings: "opsz" 12;
      font-weight: 500;
      font-size: 13px;
      letter-spacing: 0.05em;
      color: var(--oxide);
      min-width: 28px;
    }
    h2.label {
      margin: 0;
      font-family: 'IBM Plex Mono', monospace;
      font-weight: 500;
      font-size: 11px;
      letter-spacing: 0.22em;
      text-transform: uppercase;
      color: var(--ink);
    }
    .section-meta {
      margin-left: auto;
      font-family: 'IBM Plex Mono', monospace;
      font-size: 10px;
      letter-spacing: 0.12em;
      color: var(--ink-soft);
      text-transform: uppercase;
    }

    /* Today's prescription — the field card */
    .field-card {
      background: var(--paper-deep);
      border: 1px solid var(--ink);
      padding: 24px 28px 26px;
      position: relative;
    }
    .field-card::before,
    .field-card::after {
      content: ''; position: absolute;
      width: 14px; height: 14px;
      border: 1px solid var(--ink);
    }
    .field-card::before { top: -1px; left: -1px; border-right: none; border-bottom: none; background: var(--paper); }
    .field-card::after  { bottom: -1px; right: -1px; border-left: none; border-top: none; background: var(--paper); }
    .field-card .corner-tr,
    .field-card .corner-bl {
      position: absolute; width: 14px; height: 14px; border: 1px solid var(--ink);
      background: var(--paper);
    }
    .field-card .corner-tr { top: -1px; right: -1px; border-left: none; border-bottom: none; }
    .field-card .corner-bl { bottom: -1px; left: -1px; border-right: none; border-top: none; }
    .field-card .title {
      font-family: 'Fraunces', serif;
      font-variation-settings: "opsz" 36;
      font-weight: 500;
      font-size: 26px;
      line-height: 1.15;
      letter-spacing: -0.015em;
      color: var(--ink);
      margin: 0 0 4px;
    }
    .field-card .purpose {
      font-family: 'IBM Plex Mono', monospace;
      font-size: 10px;
      letter-spacing: 0.18em;
      text-transform: uppercase;
      color: var(--oxide);
      margin-bottom: 16px;
    }
    .field-card .description {
      font-size: 14px;
      line-height: 1.65;
      color: var(--ink);
      white-space: pre-line;
      max-height: 340px;
      overflow: auto;
    }
    .field-card .description::-webkit-scrollbar { width: 4px; }
    .field-card .description::-webkit-scrollbar-thumb { background: var(--rule); }

    /* Readiness verdict */
    .verdict {
      display: flex; flex-direction: column; gap: 14px;
      height: 100%;
    }
    .verdict-pill {
      display: inline-flex; align-items: baseline; gap: 12px;
      padding: 14px 18px;
      border: 1px solid var(--ink);
      background: var(--paper);
      position: relative;
    }
    .verdict-pill.green  { border-color: var(--forest); }
    .verdict-pill.amber  { border-color: var(--ochre); }
    .verdict-pill.red    { border-color: var(--oxide); }
    .verdict-pill .dot {
      width: 10px; height: 10px; border-radius: 50%;
      align-self: center;
    }
    .green .dot  { background: var(--forest); }
    .amber .dot  { background: var(--ochre); box-shadow: 0 0 0 4px rgba(193,139,61,0.18); }
    .red .dot    { background: var(--oxide); animation: pulse 1.6s ease-in-out infinite; }
    @keyframes pulse {
      0%,100% { box-shadow: 0 0 0 0 rgba(200,54,45,0.5); }
      50%     { box-shadow: 0 0 0 8px rgba(200,54,45,0); }
    }
    .verdict-pill .word {
      font-family: 'Fraunces', serif;
      font-variation-settings: "opsz" 24;
      font-style: italic;
      font-weight: 500;
      font-size: 22px;
      letter-spacing: -0.01em;
    }
    .verdict-pill .micro {
      margin-left: auto;
      font-family: 'IBM Plex Mono', monospace;
      font-size: 10px;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      color: var(--ink-soft);
    }
    .reasons {
      list-style: none; padding: 0; margin: 0;
      font-family: 'IBM Plex Mono', monospace;
      font-size: 12px;
      line-height: 1.65;
    }
    .reasons li {
      padding: 6px 0;
      border-bottom: 1px dotted var(--rule);
      color: var(--ink);
      display: flex; gap: 10px;
    }
    .reasons li::before {
      content: '——';
      color: var(--ink-soft);
      letter-spacing: -0.2em;
    }
    .reasons li:last-child { border-bottom: none; }

    /* Data cards */
    .data-card { position: relative; }
    .stat-row {
      display: flex; align-items: baseline; gap: 16px;
      margin: 0 0 12px;
      font-family: 'IBM Plex Mono', monospace;
    }
    .stat-row .big {
      font-family: 'Fraunces', serif;
      font-variation-settings: "opsz" 48;
      font-weight: 500;
      font-size: 38px;
      line-height: 1;
      letter-spacing: -0.02em;
      color: var(--ink);
    }
    .stat-row .unit {
      font-size: 11px;
      letter-spacing: 0.14em;
      text-transform: uppercase;
      color: var(--ink-soft);
    }
    .stat-row .delta {
      margin-left: auto;
      font-size: 11px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      padding: 4px 8px;
      border: 1px solid var(--rule);
    }
    .stat-row .delta.up   { color: var(--oxide); border-color: var(--oxide); }
    .stat-row .delta.down { color: var(--forest); border-color: var(--forest); }
    .stat-row .delta.flat { color: var(--ink-soft); }

    .empty {
      font-family: 'IBM Plex Mono', monospace;
      font-size: 11px;
      letter-spacing: 0.12em;
      color: var(--ink-soft);
      padding: 24px 0;
      text-align: center;
      border: 1px dashed var(--rule);
    }

    /* Race milestones strip */
    .milestones {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 0;
      border-top: 1px solid var(--rule);
      border-bottom: 1px solid var(--rule);
      margin: -8px 0 36px;
    }
    @media (max-width: 980px) {
      .milestones { grid-template-columns: 1fr 1fr; }
    }
    @media (max-width: 540px) {
      .milestones { grid-template-columns: 1fr; }
    }
    .milestone {
      padding: 16px 18px 14px;
      border-right: 1px solid var(--rule);
      display: grid;
      grid-template-areas:
        "rank name"
        "when countdown";
      grid-template-columns: auto 1fr;
      gap: 2px 14px;
      align-items: baseline;
      text-decoration: none;
      color: var(--ink);
      position: relative;
      transition: background 120ms ease;
    }
    .milestone:last-child { border-right: none; }
    @media (max-width: 980px) {
      .milestone:nth-child(2n) { border-right: none; }
    }
    @media (max-width: 540px) {
      .milestone { border-right: none; border-bottom: 1px solid var(--rule); }
      .milestone:last-child { border-bottom: none; }
    }
    .milestone.done {
      color: var(--ink-soft);
      background: repeating-linear-gradient(
        135deg, transparent 0 4px, rgba(28,31,42,0.025) 4px 5px);
    }
    .milestone.a-race {
      background: rgba(200,54,45,0.04);
    }
    .milestone.a-race:hover { background: rgba(200,54,45,0.08); }
    .milestone:hover { background: rgba(28,31,42,0.025); }
    .m-rank {
      grid-area: rank;
      font-family: 'Fraunces', serif;
      font-style: italic;
      font-weight: 500;
      font-size: 13px;
      color: var(--oxide);
    }
    .milestone.done .m-rank { color: var(--ink-soft); }
    .m-name {
      grid-area: name;
      font-family: 'Fraunces', serif;
      font-variation-settings: "opsz" 24;
      font-weight: 500;
      font-size: 18px;
      letter-spacing: -0.01em;
      line-height: 1.15;
    }
    .milestone.a-race .m-name { font-weight: 600; }
    .m-when {
      grid-area: when;
      font-family: 'IBM Plex Mono', monospace;
      font-size: 10px;
      letter-spacing: 0.18em;
      text-transform: uppercase;
      color: var(--ink-soft);
    }
    .m-countdown {
      grid-area: countdown;
      justify-self: end;
      font-family: 'IBM Plex Mono', monospace;
      font-size: 13px;
      font-weight: 500;
      color: var(--ink);
    }
    .m-countdown small {
      font-size: 10px;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      color: var(--ink-soft);
    }
    .m-countdown em {
      font-style: normal;
      font-size: 10px;
      letter-spacing: 0.14em;
      text-transform: uppercase;
      color: var(--ink-soft);
    }
    .milestone.a-race .m-countdown { color: var(--oxide); }

    /* Footer rule with coordinates */
    footer.coords {
      margin-top: 56px;
      padding-top: 18px;
      border-top: 1px solid var(--rule);
      display: flex; justify-content: space-between;
      font-family: 'IBM Plex Mono', monospace;
      font-size: 10px;
      letter-spacing: 0.14em;
      text-transform: uppercase;
      color: var(--ink-soft);
    }
  </style>
</head>
<body>
  <div class="shell">
    <header class="masthead">
      <div>
        <div class="brand">Two&nbsp;Oceans <em>&nbsp;·&nbsp;a&nbsp;log</em></div>
        <div class="tagline">Norwegian Singles · Cape Town · 03 April 2027</div>
      </div>
      <div class="stamp">
        <span class="big">{{ today_long }}</span>
        wk {{ plan_week }} / 48 &nbsp;·&nbsp; phase: {{ phase_short }} &nbsp;·&nbsp; tgt {{ target_h }}h
        <br>
        <button class="refresh" hx-get="/api/today" hx-target="#today" hx-swap="innerHTML"
                hx-on::after-request="htmx.trigger('#readiness','refresh'); htmx.trigger('#hrv','refresh'); htmx.trigger('#rhr','refresh'); htmx.trigger('#sleep','refresh'); htmx.trigger('#stress','refresh'); htmx.trigger('#volume','refresh')">
          ↻ refresh log
        </button>
      </div>
    </header>

    <nav class="milestones">
      {% for m in milestones %}
      <a class="milestone {% if m.done %}done{% endif %} {% if m.a_race %}a-race{% endif %}">
        <span class="m-rank">{{ m.rank }}</span>
        <span class="m-name">{{ m.name }}</span>
        <span class="m-when">{{ m.when }}</span>
        <span class="m-countdown">
          {% if m.done %}<em>completed</em>
          {% else %}{{ m.days }}<small>&nbsp;d</small>{% endif %}
        </span>
      </a>
      {% endfor %}
    </nav>

    <div class="grid">
      <section class="section span-2">
        <div class="section-head">
          <span class="roman">I.</span>
          <h2 class="label">Today's session</h2>
          <span class="section-meta">field card</span>
        </div>
        <div id="today" hx-get="/api/today" hx-trigger="load" hx-swap="innerHTML"></div>
      </section>

      <section class="section">
        <div class="section-head">
          <span class="roman">II.</span>
          <h2 class="label">Readiness</h2>
          <span class="section-meta">autonomic</span>
        </div>
        <div id="readiness"
             hx-get="/api/readiness" hx-trigger="load,refresh" hx-swap="innerHTML"></div>
      </section>

      <section class="section data-card">
        <div class="section-head">
          <span class="roman">III.</span>
          <h2 class="label">HRV · rMSSD</h2>
          <span class="section-meta">60 d</span>
        </div>
        <div id="hrv" hx-get="/api/hrv" hx-trigger="load,refresh" hx-swap="innerHTML"></div>
      </section>

      <section class="section data-card">
        <div class="section-head">
          <span class="roman">IV.</span>
          <h2 class="label">Resting HR</h2>
          <span class="section-meta">60 d</span>
        </div>
        <div id="rhr" hx-get="/api/rhr" hx-trigger="load,refresh" hx-swap="innerHTML"></div>
      </section>

      <section class="section data-card">
        <div class="section-head">
          <span class="roman">V.</span>
          <h2 class="label">Sleep</h2>
          <span class="section-meta">30 d</span>
        </div>
        <div id="sleep" hx-get="/api/sleep" hx-trigger="load,refresh" hx-swap="innerHTML"></div>
      </section>

      <section class="section data-card">
        <div class="section-head">
          <span class="roman">VI.</span>
          <h2 class="label">Stress</h2>
          <span class="section-meta">60 d</span>
        </div>
        <div id="stress" hx-get="/api/stress" hx-trigger="load,refresh" hx-swap="innerHTML"></div>
      </section>

      <section class="section span-3 data-card">
        <div class="section-head">
          <span class="roman">VII.</span>
          <h2 class="label">Weekly volume · actual vs. prescribed</h2>
          <span class="section-meta">12 wk</span>
        </div>
        <div id="volume" hx-get="/api/volume" hx-trigger="load,refresh" hx-swap="innerHTML"></div>
      </section>
    </div>

    <footer class="coords">
      <span>59°08′N&nbsp;&nbsp;09°39′E&nbsp;&nbsp;Porsgrunn</span>
      <span>33°57′S&nbsp;&nbsp;18°27′E&nbsp;&nbsp;Cape Town</span>
    </footer>
  </div>
</body>
</html>""")


TODAY_PARTIAL = Template("""
<div class="field-card">
  <div class="corner-tr"></div>
  <div class="corner-bl"></div>
  <div class="purpose">{{ weekday }} · {{ purpose }}</div>
  <h3 class="title">{{ title }}</h3>
  <div class="description">{{ description }}</div>
</div>
""")


READINESS_PARTIAL = Template("""
<div class="verdict">
  <div class="verdict-pill {{ light }}">
    <span class="dot"></span>
    <span class="word">{{ verdict_word }}</span>
    {% if hrv_status %}<span class="micro">HRV {{ hrv_status|lower }} · {{ hrv_last_night }} ms</span>{% endif %}
  </div>
  <ul class="reasons">
    {% for r in reasons %}<li><span>{{ r }}</span></li>{% endfor %}
  </ul>
</div>
""")


CARD_WITH_STAT = Template("""
<div class="stat-row">
  <span class="big">{{ stat_big }}</span>
  <span class="unit">{{ stat_unit }}</span>
  {% if delta %}<span class="delta {{ delta_dir }}">{{ delta }}</span>{% endif %}
</div>
{{ chart|safe }}
""")


# ─── Routes ───────────────────────────────────────────────────────────────

VERDICT_WORDS = {"green": "Proceed", "amber": "Restrain", "red": "Stand&nbsp;down"}


RACES = [
    (date(2026, 7, 12), "i.",   "Porsgrunn parkrun",  "5 K benchmark", False),
    (date(2026, 9, 12), "ii.",  "Oslo Half",          "21 K · fitness check", False),
    (date(2027, 2, 14), "iii.", "Sevilla Marathon",   "42 K · qualifier", False),
    (date(2027, 4, 3),  "iv.",  "Two Oceans Ultra",   "56 K · A-race", True),
]


@app.get("/", response_class=HTMLResponse)
async def index():
    today = date.today()
    p = prescription_for(today)
    phase_short = p.phase.split("·")[-1].strip() if "·" in p.phase else p.phase
    milestones = []
    for d, rank, name, when, a_race in RACES:
        days = (d - today).days
        milestones.append({
            "rank": rank, "name": name, "when": when,
            "days": days if days >= 0 else 0,
            "done": days < 0,
            "a_race": a_race,
        })
    return PAGE.render(
        today_long=today.strftime("%A %d %b %Y").lower(),
        plan_week=p.plan_week,
        phase_short=phase_short.split(" + ")[0],
        target_h=f"{p.target_hours:.1f}",
        milestones=milestones,
        PAPER=PAPER, PAPER_DEEP=PAPER_DEEP, INK=INK, INK_SOFT=INK_SOFT,
        RULE=RULE, OXIDE=OXIDE, FOREST=FOREST, OCHRE=OCHRE,
    )


@app.get("/api/today", response_class=HTMLResponse)
async def api_today():
    p = prescription_for(date.today())
    return TODAY_PARTIAL.render(
        weekday=p.weekday, title=p.title, purpose=p.purpose, description=p.description,
    )


@app.get("/api/readiness", response_class=HTMLResponse)
async def api_readiness():
    light, reasons = readiness_verdict()
    hrv = load_hrv_summaries()
    last = hrv.iloc[-1] if not hrv.empty else None
    return READINESS_PARTIAL.render(
        light=light,
        verdict_word=VERDICT_WORDS[light],
        reasons=reasons,
        hrv_status=last["status"] if last is not None else None,
        hrv_last_night=int(last["last_night_avg"]) if last is not None and pd.notna(last["last_night_avg"]) else "—",
    )


@app.get("/api/hrv", response_class=HTMLResponse)
async def api_hrv():
    df = load_hrv_summaries()
    if df.empty:
        return CARD_WITH_STAT.render(stat_big="—", stat_unit="ms", chart=hrv_chart())
    last = df.iloc[-1]
    week = int(last["weekly_avg"]) if pd.notna(last["weekly_avg"]) else "—"
    nightly = int(last["last_night_avg"]) if pd.notna(last["last_night_avg"]) else "—"
    delta_dir = "flat"
    delta_str = ""
    if isinstance(week, int) and isinstance(nightly, int):
        d = nightly - week
        delta_dir = "down" if d >= 0 else "up"   # higher HRV is good
        delta_str = f"{d:+d} vs 7d"
    return CARD_WITH_STAT.render(
        stat_big=nightly, stat_unit="rMSSD last night", delta=delta_str, delta_dir=delta_dir,
        chart=hrv_chart(),
    )


@app.get("/api/rhr", response_class=HTMLResponse)
async def api_rhr():
    df = load_daily_summaries()
    if df.empty:
        return CARD_WITH_STAT.render(stat_big="—", stat_unit="bpm", chart=rhr_chart())
    last = df.iloc[-1]
    baseline = df.tail(14).iloc[:-1]["rhr"].mean() if len(df) > 1 else None
    delta_str = ""
    delta_dir = "flat"
    if baseline:
        d = int(last["rhr"] - baseline)
        delta_dir = "up" if d > 0 else "down"   # higher RHR is bad
        delta_str = f"{d:+d} vs 14d"
    return CARD_WITH_STAT.render(
        stat_big=int(last["rhr"]), stat_unit="bpm today", delta=delta_str, delta_dir=delta_dir,
        chart=rhr_chart(),
    )


@app.get("/api/sleep", response_class=HTMLResponse)
async def api_sleep():
    df = load_sleep_summaries()
    if df.empty:
        return CARD_WITH_STAT.render(stat_big="—", stat_unit="h", chart=sleep_chart())
    last = df.iloc[-1]
    week = df.tail(7)["total_h"].mean() if len(df) >= 3 else None
    delta_str = ""
    delta_dir = "flat"
    if week:
        d = last["total_h"] - week
        delta_dir = "down" if d >= 0 else "up"  # more sleep is good
        delta_str = f"{d:+.1f}h vs 7d"
    return CARD_WITH_STAT.render(
        stat_big=f"{last['total_h']:.1f}", stat_unit="h last night",
        delta=delta_str, delta_dir=delta_dir, chart=sleep_chart(),
    )


@app.get("/api/stress", response_class=HTMLResponse)
async def api_stress():
    df = load_stress_summaries()
    if df.empty:
        return CARD_WITH_STAT.render(stat_big="—", stat_unit="/100", chart=stress_chart())
    last = df.iloc[-1]
    return CARD_WITH_STAT.render(
        stat_big=int(last["avg_stress"]), stat_unit="avg today",
        delta=f"peak {int(last['max_stress'])}" if pd.notna(last["max_stress"]) else "",
        delta_dir="flat", chart=stress_chart(),
    )


@app.get("/api/volume", response_class=HTMLResponse)
async def api_volume():
    df = weekly_volume()
    if df.empty:
        return CARD_WITH_STAT.render(stat_big="—", stat_unit="h this week", chart=volume_chart())
    last = df.iloc[-1]
    return CARD_WITH_STAT.render(
        stat_big=f"{last['actual_h']:.1f}",
        stat_unit=f"h done · {last['target_h']:.1f} h target",
        delta=f"wk {int(last['plan_week'])}", delta_dir="flat",
        chart=volume_chart(),
    )
