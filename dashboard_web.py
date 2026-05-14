"""FastAPI + HTMX training dashboard.

Run:
    uv run uvicorn dashboard_web:app --reload

Open http://localhost:8000.

One full-page render with HTMX partial endpoints for the refreshable cards.
Each card is a server-rendered HTML fragment containing a Plotly chart div.
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


# ─── Data loaders ────────────────────────────────────────────────────────

def load_hrv_summaries() -> pd.DataFrame:
    """One row per day with Garmin's own HRV summary (richer than rolling stats)."""
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
            # File may be wrapped or flat
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
                "feedback": d.get("sleepScoreFeedback") or "",
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
                "min_hr": obj.get("minHeartRate"),
                "max_hr": obj.get("maxHeartRate"),
                "steps": obj.get("totalSteps"),
                "active_kcal": obj.get("activeKilocalories"),
                "vigorous_min": obj.get("vigorousIntensityMinutes"),
                "moderate_min": obj.get("moderateIntensityMinutes"),
                "floors": obj.get("floorsAscended"),
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

def garmin_readiness() -> tuple[str, list[str]]:
    """Combine Garmin's HRV status + simple checks. Returns (light, reasons)."""
    hrv = load_hrv_summaries()
    stress = load_stress_summaries()
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
            amber(f"HRV unbalanced (last night {last['last_night_avg']} ms)")
        elif status == "POOR":
            red(f"HRV poor (last night {last['last_night_avg']} ms)")
        # week trend
        if len(hrv) >= 7 and pd.notna(last["weekly_avg"]):
            week_ago = hrv.iloc[-7]["weekly_avg"]
            if pd.notna(week_ago):
                delta = last["weekly_avg"] - week_ago
                if delta < -3:
                    amber(f"weekly HRV down {delta:+.0f} ms in 7d")

    if not stress.empty:
        last = stress.iloc[-1]
        if pd.notna(last["avg_stress"]) and last["avg_stress"] > 50:
            amber(f"avg stress yesterday {int(last['avg_stress'])}/100")

    sleep = load_sleep_summaries()
    if not sleep.empty:
        last = sleep.iloc[-1]
        if last["total_h"] < 6:
            red(f"slept only {last['total_h']:.1f}h last night")
        elif last["total_h"] < 7:
            amber(f"slept {last['total_h']:.1f}h last night")
        if len(sleep) >= 7:
            week_avg = sleep.tail(7)["total_h"].mean()
            if week_avg < 7:
                amber(f"7-day sleep avg {week_avg:.1f}h (target 7.5h)")

    daily = load_daily_summaries()
    if not daily.empty and len(daily) >= 14:
        last = daily.iloc[-1]
        baseline = daily.iloc[-14:-1]["rhr"].mean()
        if pd.notna(last["rhr"]) and pd.notna(baseline):
            delta = last["rhr"] - baseline
            if delta > 5:
                red(f"RHR {int(last['rhr'])} vs baseline {baseline:.0f} (+{delta:.0f})")
            elif delta > 3:
                amber(f"RHR {int(last['rhr'])} vs baseline {baseline:.0f} (+{delta:.0f})")

    if not reasons:
        reasons.append("HRV balanced, stress nominal")
    return light, reasons


# ─── Chart builders ──────────────────────────────────────────────────────

def chart_html(fig: go.Figure, div_id: str) -> str:
    return fig.to_html(include_plotlyjs="cdn", div_id=div_id, full_html=False, config={"displayModeBar": False})


def hrv_chart() -> str:
    df = load_hrv_summaries()
    if df.empty:
        return "<div class='text-zinc-500 text-sm'>no HRV data</div>"
    df = df.tail(60)
    fig = go.Figure()
    # Baseline range as filled band
    fig.add_trace(go.Scatter(
        x=df["date"], y=df["baseline_balanced_upper"],
        fill=None, mode="lines", line=dict(width=0), showlegend=False, hoverinfo="skip",
    ))
    fig.add_trace(go.Scatter(
        x=df["date"], y=df["baseline_balanced_low"],
        fill="tonexty", mode="lines", line=dict(width=0),
        fillcolor="rgba(34,197,94,0.15)", name="balanced range",
    ))
    fig.add_trace(go.Scatter(
        x=df["date"], y=df["last_night_avg"], mode="lines+markers",
        line=dict(color="#22c55e", width=2), marker=dict(size=5),
        name="last night",
    ))
    fig.add_trace(go.Scatter(
        x=df["date"], y=df["weekly_avg"], mode="lines",
        line=dict(color="#0ea5e9", width=2, dash="dot"), name="7-day avg",
    ))
    fig.update_layout(
        height=260, margin=dict(l=20, r=10, t=10, b=20),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#e4e4e7"),
        legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="right", x=1),
        xaxis=dict(gridcolor="#27272a"), yaxis=dict(gridcolor="#27272a", title="rMSSD (ms)"),
    )
    return chart_html(fig, "hrv-chart")


def stress_chart() -> str:
    df = load_stress_summaries()
    if df.empty:
        return "<div class='text-zinc-500 text-sm'>no stress data</div>"
    df = df.tail(60)
    fig = go.Figure()
    fig.add_trace(go.Bar(x=df["date"], y=df["avg_stress"], name="avg stress",
                        marker_color="#f59e0b"))
    fig.add_trace(go.Scatter(x=df["date"], y=df["max_stress"], mode="lines",
                            line=dict(color="#ef4444", width=1, dash="dot"), name="max"))
    fig.update_layout(
        height=220, margin=dict(l=20, r=10, t=10, b=20),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#e4e4e7"),
        legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="right", x=1),
        xaxis=dict(gridcolor="#27272a"), yaxis=dict(gridcolor="#27272a", title="stress 0-100"),
    )
    return chart_html(fig, "stress-chart")


def sleep_chart() -> str:
    df = load_sleep_summaries()
    if df.empty:
        return "<div class='text-zinc-500 text-sm'>no sleep data</div>"
    df = df.tail(30)
    fig = go.Figure()
    fig.add_trace(go.Bar(x=df["date"], y=df["deep_h"], name="deep",
                        marker_color="#1d4ed8"))
    fig.add_trace(go.Bar(x=df["date"], y=df["rem_h"], name="REM",
                        marker_color="#7c3aed"))
    fig.add_trace(go.Bar(x=df["date"], y=df["light_h"], name="light",
                        marker_color="#60a5fa"))
    fig.add_trace(go.Bar(x=df["date"], y=df["awake_h"], name="awake",
                        marker_color="#52525b"))
    fig.add_hline(y=7.5, line_dash="dash", line_color="#22c55e",
                  annotation_text="target", annotation_position="right")
    fig.update_layout(
        height=240, margin=dict(l=20, r=10, t=10, b=20),
        barmode="stack",
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#e4e4e7"),
        legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="right", x=1),
        xaxis=dict(gridcolor="#27272a"), yaxis=dict(gridcolor="#27272a", title="hours"),
    )
    return chart_html(fig, "sleep-chart")


def rhr_chart() -> str:
    df = load_daily_summaries()
    if df.empty:
        return "<div class='text-zinc-500 text-sm'>no daily summary data</div>"
    df = df.tail(60)
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df["date"], y=df["rhr"], mode="lines+markers",
                            line=dict(color="#ef4444", width=2),
                            marker=dict(size=4), name="RHR"))
    if "rhr_7d" in df.columns:
        fig.add_trace(go.Scatter(x=df["date"], y=df["rhr_7d"], mode="lines",
                                line=dict(color="#f97316", width=2, dash="dot"),
                                name="7-day"))
    fig.update_layout(
        height=240, margin=dict(l=20, r=10, t=10, b=20),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#e4e4e7"),
        legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="right", x=1),
        xaxis=dict(gridcolor="#27272a"), yaxis=dict(gridcolor="#27272a", title="bpm"),
    )
    return chart_html(fig, "rhr-chart")


def volume_chart() -> str:
    df = weekly_volume()
    if df.empty:
        return "<div class='text-zinc-500 text-sm'>no activities synced yet</div>"
    df = df.tail(12)
    fig = go.Figure()
    fig.add_trace(go.Bar(x=df["week"], y=df["actual_h"], name="actual",
                        marker_color="#22c55e"))
    fig.add_trace(go.Bar(x=df["week"], y=df["target_h"], name="target",
                        marker_color="#3f3f46"))
    fig.update_layout(
        height=240, margin=dict(l=20, r=10, t=10, b=20),
        barmode="group",
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#e4e4e7"),
        legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="right", x=1),
        xaxis=dict(gridcolor="#27272a"), yaxis=dict(gridcolor="#27272a", title="hours"),
    )
    return chart_html(fig, "volume-chart")


# ─── Templates ────────────────────────────────────────────────────────────

PAGE = Template("""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Two Oceans 2027 · dashboard</title>
  <script src="https://unpkg.com/htmx.org@2.0.4"></script>
  <script src="https://cdn.tailwindcss.com"></script>
  <script>
    // HTMX doesn't run <script> tags in swapped HTML. Re-create them so
    // Plotly (and any other inline JS) actually executes after each swap.
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
    body { background: #0a0a0a; color: #e4e4e7; font-family: ui-sans-serif, system-ui; }
    .card { background: #18181b; border: 1px solid #27272a; border-radius: 12px; padding: 1rem 1.25rem; }
    .pill { display: inline-flex; align-items: center; gap: .35rem; padding: .15rem .6rem; border-radius: 999px; font-size: .75rem; }
    .green { background: rgba(34,197,94,.15); color: #4ade80; }
    .amber { background: rgba(245,158,11,.15); color: #fbbf24; }
    .red   { background: rgba(239,68,68,.15);  color: #f87171; }
    h1 { font-weight: 600; letter-spacing: -.01em; }
    h2 { font-weight: 500; color: #a1a1aa; font-size: .875rem; text-transform: uppercase; letter-spacing: .05em; }
  </style>
</head>
<body class="px-6 py-6 max-w-7xl mx-auto">
  <header class="flex items-end justify-between mb-6">
    <div>
      <h1 class="text-2xl">Two Oceans 2027</h1>
      <div class="text-sm text-zinc-400">{{ today }} · plan week {{ plan_week }} · target {{ target_h }}h · {{ phase }}</div>
    </div>
    <button class="text-sm text-zinc-400 hover:text-zinc-200"
            hx-get="/api/today" hx-target="#today" hx-swap="innerHTML">refresh</button>
  </header>

  <div class="grid grid-cols-1 lg:grid-cols-3 gap-4 mb-4">
    <div class="card lg:col-span-1" id="today" hx-get="/api/today" hx-trigger="load" hx-swap="innerHTML">
      <div class="text-zinc-500">loading…</div>
    </div>
    <div class="card lg:col-span-2" id="readiness" hx-get="/api/readiness" hx-trigger="load" hx-swap="innerHTML">
      <div class="text-zinc-500">loading…</div>
    </div>
  </div>

  <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
    <div class="card" id="hrv" hx-get="/api/hrv" hx-trigger="load" hx-swap="innerHTML"></div>
    <div class="card" id="rhr" hx-get="/api/rhr" hx-trigger="load" hx-swap="innerHTML"></div>
    <div class="card" id="sleep" hx-get="/api/sleep" hx-trigger="load" hx-swap="innerHTML"></div>
    <div class="card" id="stress" hx-get="/api/stress" hx-trigger="load" hx-swap="innerHTML"></div>
    <div class="card lg:col-span-2" id="volume" hx-get="/api/volume" hx-trigger="load" hx-swap="innerHTML"></div>
  </div>
</body>
</html>""")

TODAY_PARTIAL = Template("""
<h2>Today's session</h2>
<div class="text-lg mt-1 mb-1 font-medium">{{ title }}</div>
<div class="text-sm text-zinc-400 mb-3">{{ purpose }}</div>
<div class="text-sm whitespace-pre-line">{{ description }}</div>
""")

READINESS_PARTIAL = Template("""
<h2>Readiness</h2>
<div class="mt-2 mb-3 flex items-center gap-3">
  <span class="pill {{ light }}">
    {% if light == 'green' %}🟢 train normally
    {% elif light == 'amber' %}🟡 easy only
    {% else %}🔴 rest / recover{% endif %}
  </span>
  {% if hrv_status %}
  <span class="text-sm text-zinc-400">HRV: {{ hrv_status|lower }} · last night {{ hrv_last_night }} ms · 7-day {{ hrv_weekly }} ms</span>
  {% endif %}
</div>
<ul class="text-sm space-y-1">
  {% for r in reasons %}
  <li class="text-zinc-300">· {{ r }}</li>
  {% endfor %}
</ul>
""")

HRV_PARTIAL = Template("""
<h2>HRV — last 60 days</h2>
<div class="mt-2">{{ chart|safe }}</div>
""")

STRESS_PARTIAL = Template("""
<h2>Stress — last 60 days</h2>
<div class="mt-2">{{ chart|safe }}</div>
""")

VOLUME_PARTIAL = Template("""
<h2>Weekly volume — actual vs target</h2>
<div class="mt-2">{{ chart|safe }}</div>
""")


# ─── Routes ───────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    today = date.today()
    p = prescription_for(today)
    return PAGE.render(
        today=today.strftime("%a %d %b %Y"),
        plan_week=p.plan_week,
        target_h=f"{p.target_hours:.1f}",
        phase=p.phase,
    )


@app.get("/api/today", response_class=HTMLResponse)
async def api_today():
    p = prescription_for(date.today())
    return TODAY_PARTIAL.render(title=p.title, purpose=p.purpose, description=p.description)


@app.get("/api/readiness", response_class=HTMLResponse)
async def api_readiness():
    light, reasons = garmin_readiness()
    hrv = load_hrv_summaries()
    last = hrv.iloc[-1] if not hrv.empty else None
    return READINESS_PARTIAL.render(
        light=light,
        reasons=reasons,
        hrv_status=last["status"] if last is not None else None,
        hrv_last_night=int(last["last_night_avg"]) if last is not None and pd.notna(last["last_night_avg"]) else "—",
        hrv_weekly=int(last["weekly_avg"]) if last is not None and pd.notna(last["weekly_avg"]) else "—",
    )


@app.get("/api/hrv", response_class=HTMLResponse)
async def api_hrv():
    return HRV_PARTIAL.render(chart=hrv_chart())


@app.get("/api/stress", response_class=HTMLResponse)
async def api_stress():
    return STRESS_PARTIAL.render(chart=stress_chart())


@app.get("/api/sleep", response_class=HTMLResponse)
async def api_sleep():
    df = load_sleep_summaries()
    last = df.iloc[-1] if not df.empty else None
    headline = ""
    if last is not None:
        headline = f"<div class='text-sm text-zinc-400 mb-2'>last night: <span class='text-zinc-200'>{last['total_h']:.1f}h</span> · deep {last['deep_h']:.1f}h · REM {last['rem_h']:.1f}h</div>"
    return f"<h2>Sleep — last 30 days</h2>{headline}<div class='mt-2'>{sleep_chart()}</div>"


@app.get("/api/rhr", response_class=HTMLResponse)
async def api_rhr():
    df = load_daily_summaries()
    last = df.iloc[-1] if not df.empty else None
    headline = ""
    if last is not None:
        baseline = df.tail(14).iloc[:-1]["rhr"].mean() if len(df) > 1 else None
        delta = (last["rhr"] - baseline) if baseline else None
        delta_str = f" ({'+' if delta and delta>0 else ''}{int(delta)} vs 14d)" if delta is not None else ""
        headline = f"<div class='text-sm text-zinc-400 mb-2'>RHR today: <span class='text-zinc-200'>{int(last['rhr'])} bpm</span>{delta_str}</div>"
    return f"<h2>Resting heart rate — last 60 days</h2>{headline}<div class='mt-2'>{rhr_chart()}</div>"


@app.get("/api/volume", response_class=HTMLResponse)
async def api_volume():
    return VOLUME_PARTIAL.render(chart=volume_chart())
