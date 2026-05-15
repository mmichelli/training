"""FastAPI + HTMX training dashboard — expedition logbook aesthetic.

Run:
    uv run uvicorn dashboard_web:app --reload --port 8765

Open http://localhost:8765.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse
from jinja2 import Template


def eu_date(value) -> str:
    """Render ISO date string or date object as DD/MM/YYYY."""
    if value is None or value == "":
        return ""
    if isinstance(value, str):
        try:
            value = date.fromisoformat(value[:10])
        except ValueError:
            return value
    return value.strftime("%d/%m/%Y")

import checkin as C
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


def load_alcohol() -> pd.DataFrame:
    """One row per day with a logged value. Days without a file = no entry."""
    rows = []
    d = DATA / "alcohol"
    if not d.exists():
        return pd.DataFrame()
    for p in sorted(d.glob("*.json")):
        try:
            obj = json.loads(p.read_text())
            rows.append({"date": p.stem, "units": float(obj.get("units") or 0)})
        except Exception:
            continue
    df = pd.DataFrame(rows)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)
    return df


def save_alcohol(d: date, units: float) -> None:
    out = DATA / "alcohol"
    out.mkdir(parents=True, exist_ok=True)
    from datetime import datetime as _dt
    (out / f"{d.isoformat()}.json").write_text(json.dumps({
        "units": round(max(0.0, units), 2),
        "logged_at": _dt.now().isoformat(timespec="seconds"),
    }, indent=2))


def alcohol_hrv_insight() -> str:
    """Short text comparing HRV on drinking days vs dry days over last 30d."""
    alc = load_alcohol()
    hrv = load_hrv_summaries()
    if alc.empty or hrv.empty:
        return ""
    cutoff = pd.Timestamp(date.today()) - pd.Timedelta(days=30)
    alc = alc[alc["date"] >= cutoff]
    hrv = hrv[["date", "last_night_avg"]].copy()
    # Drinking on day N most affects HRV on the night of day N → recorded on day N+1.
    alc = alc.assign(next_day=alc["date"] + pd.Timedelta(days=1))
    joined = hrv.merge(alc[["next_day", "units"]], left_on="date", right_on="next_day", how="left")
    joined["units"] = joined["units"].fillna(0)
    drank = joined[joined["units"] > 0]["last_night_avg"].dropna()
    dry = joined[joined["units"] == 0]["last_night_avg"].dropna()
    if len(drank) < 2 or len(dry) < 3:
        return ""
    delta = drank.mean() - dry.mean()
    return f"HRV after drinking days: {drank.mean():.0f} ms (n={len(drank)}) · dry: {dry.mean():.0f} ms (n={len(dry)}) · Δ {delta:+.0f}"


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


def load_weight() -> pd.DataFrame:
    p = DATA / "weight" / "all.json"
    if not p.exists():
        return pd.DataFrame()
    try:
        obj = json.loads(p.read_text())
    except Exception:
        return pd.DataFrame()
    rows = []
    for s in obj.get("dailyWeightSummaries") or []:
        lw = s.get("latestWeight") or {}
        if lw.get("weight") is None:
            continue
        rows.append({
            "date": s["summaryDate"],
            "kg": lw["weight"] / 1000.0,   # API returns grams
            "source": lw.get("sourceType", ""),
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)


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

def signals_text() -> str:
    """Compact text dump of latest signals — passed to coach.evaluate()."""
    out = []
    light, reasons = "—", []  # set below
    light, reasons = readiness_verdict()
    out.append(f"Readiness: {light.upper()}")
    for r in reasons:
        out.append(f"  · {r}")

    hrv = load_hrv_summaries()
    if not hrv.empty:
        l = hrv.iloc[-1]
        out.append(f"HRV last night {l['last_night_avg']:.0f} ms · 7d {l['weekly_avg']:.0f} ms · status {l['status']}")

    rhr = load_daily_summaries()
    if not rhr.empty:
        l = rhr.iloc[-1]
        out.append(f"RHR today {l['rhr']:.0f} · 7d {l['rhr_7d']:.0f}")

    sleep = load_sleep_summaries()
    if not sleep.empty:
        wk = sleep.tail(7)["total_h"].mean()
        out.append(f"Sleep last night {sleep.iloc[-1]['total_h']:.1f}h · 7d avg {wk:.1f}h")

    weight = load_weight()
    if not weight.empty:
        l = weight.iloc[-1]
        out.append(f"Weight {l['kg']:.1f} kg · to 75 kg: {l['kg']-75:+.1f} kg")

    return "\n".join(out)


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

    # Data-freshness check — if the watch hasn't synced recently, say so plainly
    # instead of inferring "all clear" from absent signals.
    today = pd.Timestamp.now().normalize()
    sources_stale = []
    for name, df, date_col in [
        ("HRV", hrv, "date"),
        ("sleep", sleep, "date"),
        ("RHR", daily, "date"),
    ]:
        if df.empty:
            sources_stale.append(f"{name} (no data)")
            continue
        last = df[date_col].max()
        days_old = (today - last).days
        if days_old >= 2:
            sources_stale.append(f"{name} ({days_old}d old)")
    if sources_stale:
        amber("watch data stale: " + ", ".join(sources_stale) + " — sync / charge before trusting")

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

    weight = load_weight()
    if not weight.empty and len(weight) >= 2:
        last = weight.iloc[-1]
        for window_days, threshold, label in [(14, 2.0, "14d"), (30, 3.0, "30d")]:
            cutoff = last["date"] - pd.Timedelta(days=window_days)
            prior = weight[weight["date"] >= cutoff]
            if len(prior) >= 2:
                drop = prior["kg"].max() - last["kg"]
                if drop >= threshold:
                    amber(f"weight {last['kg']:.1f} kg · -{drop:.1f} kg in {label} (watch recovery)")
                    break

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
            tickformatstops=[
                dict(dtickrange=[None, 86400000], value="%d/%m %H:%M"),
                dict(dtickrange=[86400000, "M1"], value="%d/%m"),
                dict(dtickrange=["M1", "M12"], value="%b %Y"),
                dict(dtickrange=["M12", None], value="%Y"),
            ],
            hoverformat="%d/%m/%Y",
        ),
        yaxis=dict(
            gridcolor=GRID, linecolor=RULE, linewidth=1, ticks="outside",
            tickcolor=RULE, ticklen=4, tickfont=dict(size=10, color=INK_SOFT),
            zeroline=False,
        ),
    )


def chart_html(fig: go.Figure, div_id: str) -> str:
    # Plotly is loaded once in the page <head>; don't bundle per-card or we
    # race the CDN load against the inline newPlot call after HTMX swap.
    return fig.to_html(include_plotlyjs=False, div_id=div_id, full_html=False,
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


def alcohol_chart() -> str:
    alc = load_alcohol().tail(30)
    hrv = load_hrv_summaries().tail(30)
    if alc.empty and hrv.empty:
        return "<div class='empty'>no entries yet</div>"
    fig = go.Figure()
    if not alc.empty:
        fig.add_trace(go.Bar(
            x=alc["date"], y=alc["units"], name="units",
            marker_color=OXIDE, marker_line=dict(width=0),
        ))
    if not hrv.empty:
        fig.add_trace(go.Scatter(
            x=hrv["date"], y=hrv["last_night_avg"], name="HRV (ms)",
            mode="lines", line=dict(color=FOREST, width=1.5),
            yaxis="y2",
        ))
    layout = chart_layout()
    layout["yaxis"] = dict(title=dict(text="units", font=dict(size=10, color=INK_SOFT)),
                           gridcolor=GRID, zeroline=False)
    layout["yaxis2"] = dict(title=dict(text="HRV", font=dict(size=10, color=INK_SOFT)),
                            overlaying="y", side="right", showgrid=False, zeroline=False)
    layout["showlegend"] = False
    fig.update_layout(**layout)
    return chart_html(fig, "alcohol-chart")


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


WEIGHT_GOAL_KG = 75.0


def weight_chart() -> str:
    df = load_weight()
    if df.empty:
        return "<div class='empty'>no weigh-ins yet</div>"
    # Resample to daily, forward-fill, take 30-day rolling mean
    s = df.set_index("date")["kg"].asfreq("D").ffill()
    rolling = s.rolling(window=30, min_periods=3).mean()
    # Overlay weekly training volume (h) on secondary axis if available
    vol = weekly_volume()
    fig = go.Figure()
    fig.add_hrect(y0=WEIGHT_GOAL_KG - 0.5, y1=WEIGHT_GOAL_KG + 0.5,
                  fillcolor="rgba(74,107,71,0.06)", line=dict(width=0),
                  layer="below")
    fig.add_hline(y=WEIGHT_GOAL_KG, line=dict(color=FOREST, width=1.2, dash="dash"),
                  annotation_text=f"goal {WEIGHT_GOAL_KG:.0f} kg",
                  annotation_position="bottom right",
                  annotation=dict(font=dict(family="'IBM Plex Mono', monospace",
                                            size=10, color=FOREST)))
    fig.add_trace(go.Scatter(
        x=df["date"], y=df["kg"], mode="markers",
        marker=dict(size=6, color=INK, line=dict(color=PAPER, width=1)),
        name="weigh-in",
    ))
    fig.add_trace(go.Scatter(
        x=rolling.index, y=rolling.values, mode="lines",
        line=dict(color=INK_SOFT, width=1.4),
        name="30-d rolling",
    ))
    if not vol.empty:
        fig.add_trace(go.Bar(
            x=pd.to_datetime(vol["week"]), y=vol["actual_h"],
            yaxis="y2", marker_color="rgba(28,31,42,0.10)",
            marker_line=dict(width=0),
            name="weekly volume",
        ))
    layout = chart_layout(height=280)
    layout.update(
        yaxis=dict(
            **layout["yaxis"],
            title=dict(text="kg", font=dict(size=10, color=INK_SOFT)),
        ),
        yaxis2=dict(
            overlaying="y", side="right", showgrid=False,
            tickfont=dict(size=10, color=INK_SOFT),
            title=dict(text="h/wk", font=dict(size=10, color=INK_SOFT)),
            linecolor="rgba(0,0,0,0)",
        ),
    )
    fig.update_layout(**layout)
    return chart_html(fig, "weight-chart")


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
  <script src="https://cdn.plot.ly/plotly-3.5.0.min.js"></script>
  <script src="https://unpkg.com/htmx.org@2.0.4"></script>
  <script>
    // HTMX strips <script> tags from swapped HTML — re-create them so
    // Plotly.newPlot(...) inline scripts actually execute.
    document.addEventListener('htmx:afterSwap', (e) => {
      e.detail.target.querySelectorAll('script').forEach((old) => {
        if (old.src && old.src.includes('plotly')) return; // already loaded in head
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
    /* Subtle paper grain — scrolls with the page so it feels like real paper */
    body {
      background-image:
        repeating-linear-gradient(0deg,  transparent 0 31px, rgba(28,31,42,0.012) 31px 32px),
        repeating-linear-gradient(90deg, transparent 0 31px, rgba(28,31,42,0.010) 31px 32px);
    }
    /* One soft topographic ring set behind the masthead, scrolls with content */
    body::before {
      content: '';
      position: absolute;
      top: -120px; left: -120px;
      width: 900px; height: 700px;
      background-image: radial-gradient(ellipse 80% 60% at 30% 35%,
        transparent 36%, rgba(28,31,42,0.012) 36%, rgba(28,31,42,0.012) 36.5%, transparent 37%,
        transparent 46%, rgba(28,31,42,0.014) 46%, rgba(28,31,42,0.014) 46.5%, transparent 47%,
        transparent 56%, rgba(28,31,42,0.016) 56%, rgba(28,31,42,0.016) 56.5%, transparent 57%,
        transparent 66%, rgba(28,31,42,0.012) 66%, rgba(28,31,42,0.012) 66.5%, transparent 67%,
        transparent 76%, rgba(28,31,42,0.010) 76%, rgba(28,31,42,0.010) 76.5%, transparent 77%);
      pointer-events: none;
      z-index: 0;
    }
    /* A second ring set further down so there's interest as you scroll */
    body::after {
      content: '';
      position: absolute;
      top: 1100px; right: -100px;
      width: 700px; height: 700px;
      background-image: radial-gradient(ellipse 70% 70% at 70% 50%,
        transparent 30%, rgba(28,31,42,0.014) 30%, rgba(28,31,42,0.014) 30.5%, transparent 31%,
        transparent 42%, rgba(28,31,42,0.016) 42%, rgba(28,31,42,0.016) 42.5%, transparent 43%,
        transparent 56%, rgba(28,31,42,0.018) 56%, rgba(28,31,42,0.018) 56.5%, transparent 57%,
        transparent 70%, rgba(28,31,42,0.012) 70%, rgba(28,31,42,0.012) 70.5%, transparent 71%);
      pointer-events: none;
      z-index: 0;
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
      gap: 8px 24px;
      padding-bottom: 18px;
      border-bottom: 1px solid var(--rule);
      margin-bottom: 28px;
    }
    .brand {
      font-family: 'Fraunces', serif;
      font-optical-sizing: auto;
      font-variation-settings: "opsz" 144, "SOFT" 100;
      font-weight: 600;
      font-size: clamp(34px, 6vw, 64px);
      line-height: 0.95;
      letter-spacing: -0.025em;
      color: var(--ink);
    }
    .brand em {
      font-style: italic;
      font-variation-settings: "opsz" 144, "SOFT" 100;
      color: var(--oxide);
      white-space: nowrap;
    }
    .tagline {
      margin-top: 8px;
      font-family: 'IBM Plex Mono', monospace;
      font-size: 11px;
      letter-spacing: 0.16em;
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
      font-size: clamp(18px, 3vw, 22px);
      letter-spacing: -0.01em;
      color: var(--ink);
      text-transform: none;
      display: block;
      margin-bottom: 2px;
    }
    .stamp .meta { display: block; }
    .sync-row {
      display: flex; flex-wrap: wrap; align-items: baseline; gap: 12px;
      margin-top: 8px;
    }
    .refresh {
      background: none; border: none; cursor: pointer;
      font-family: 'IBM Plex Mono', monospace;
      font-size: 10px; letter-spacing: 0.15em;
      color: var(--ink-soft); text-transform: uppercase;
      padding: 4px 0;
      border-bottom: 1px solid var(--rule);
    }
    .refresh:hover { color: var(--oxide); border-color: var(--oxide); }
    .refresh:disabled { opacity: 0.4; cursor: progress; }
    .htmx-indicator {
      display: none;
      font-family: 'IBM Plex Mono', monospace;
      font-size: 10px; letter-spacing: 0.14em;
      text-transform: uppercase;
      color: var(--oxide);
    }
    .htmx-indicator::before {
      content: '◌';
      display: inline-block;
      margin-right: 4px;
      animation: spin 0.9s linear infinite;
    }
    @keyframes spin { from { transform: rotate(0); } to { transform: rotate(360deg); } }
    .htmx-request .htmx-indicator,
    .htmx-request.htmx-indicator { display: inline-block; }
    .sync-result {
      font-family: 'IBM Plex Mono', monospace;
      font-size: 10px; letter-spacing: 0.1em;
      text-transform: lowercase;
      color: var(--ink-soft);
    }
    .sync-result.ok  { color: var(--forest); }
    .sync-result.err { color: var(--oxide); }

    /* Mobile: stack and compress masthead */
    @media (max-width: 720px) {
      header.masthead {
        grid-template-columns: 1fr;
        align-items: start;
        gap: 4px;
        margin-bottom: 22px;
      }
      .brand { font-size: clamp(34px, 11vw, 48px); }
      .brand em { display: inline; }
      .tagline { margin-top: 4px; font-size: 10px; letter-spacing: 0.14em; }
      .stamp {
        text-align: left;
        margin-top: 14px;
        padding-top: 14px;
        border-top: 1px dotted var(--rule);
        display: grid;
        grid-template-columns: 1fr auto;
        align-items: end;
        gap: 4px 16px;
        line-height: 1.45;
      }
      .stamp .big {
        grid-column: 1 / span 2;
        margin-bottom: 4px;
        font-size: 18px;
      }
      .stamp .meta { font-size: 10px; }
      .refresh { margin-top: 0; align-self: end; }
    }

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

    /* Field maxim — italic margin note */
    .maxim {
      margin: 22px 0 0;
      padding: 18px 4px 4px;
      border-top: 1px dotted var(--rule);
      font-family: 'Fraunces', serif;
      font-variation-settings: "opsz" 24;
      font-style: italic;
      font-weight: 400;
      font-size: 17px;
      line-height: 1.45;
      letter-spacing: -0.005em;
      color: var(--ink);
      display: grid;
      grid-template-columns: auto 1fr;
      gap: 0 14px;
    }
    .maxim-mark {
      font-family: 'Fraunces', serif;
      font-style: normal;
      font-size: 22px;
      color: var(--oxide);
      line-height: 1;
      margin-top: 2px;
    }
    .maxim cite {
      grid-column: 2;
      display: block;
      margin-top: 6px;
      font-family: 'IBM Plex Mono', monospace;
      font-style: normal;
      font-size: 10px;
      letter-spacing: 0.18em;
      text-transform: uppercase;
      color: var(--ink-soft);
    }

    /* Journey progress — the expedition line + target-volume curve underneath */
    .journey {
      position: relative;
      margin: 28px 0 44px;
      padding: 0 4px;
      height: 66px;
    }
    .journey-line {
      position: absolute; left: 0; right: 0; top: 17px;
      height: 1px; background: var(--rule);
    }
    .journey-fill {
      position: absolute; left: 0; top: 16px;
      height: 3px; background: var(--ink);
    }
    .journey-tick {
      position: absolute; top: 12px;
      width: 1px; height: 11px;
      background: var(--ink);
    }
    .journey-tick.race { background: var(--oxide); height: 15px; top: 10px; width: 2px; }
    .journey-tick.today {
      background: var(--oxide); height: 19px; top: 8px; width: 2px;
      box-shadow: 0 0 0 4px rgba(200,54,45,0.10);
    }
    .journey-label {
      position: absolute;
      font-family: 'IBM Plex Mono', monospace;
      font-size: 9px;
      letter-spacing: 0.14em;
      text-transform: uppercase;
      color: var(--ink-soft);
      transform: translateX(-50%);
      top: 28px;
      white-space: nowrap;
    }
    .journey-label.today { color: var(--oxide); font-weight: 500; }
    .journey-curve {
      position: absolute; top: 44px; left: 0; right: 0;
      width: 100%; height: 22px;
      overflow: visible;
    }
    .journey-curve-labels {
      position: absolute; top: 50px; left: 0; right: 0;
      display: flex; justify-content: space-between;
      font-family: 'IBM Plex Mono', monospace;
      font-size: 9px;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      color: var(--ink-soft);
      padding: 0 2px;
    }

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

    /* Check-in panel */
    .checkin-wrap {
      display: grid;
      grid-template-columns: 1fr 1.4fr;
      gap: 36px;
    }
    @media (max-width: 880px) { .checkin-wrap { grid-template-columns: 1fr; gap: 24px; } }

    .streak-tile {
      background: var(--paper-deep);
      border: 1px solid var(--ink);
      padding: 22px 24px;
      position: relative;
    }
    .streak-tile::before {
      content: ''; position: absolute; top: -1px; left: -1px;
      width: 14px; height: 14px; border: 1px solid var(--ink);
      border-right: none; border-bottom: none; background: var(--paper);
    }
    .streak-num {
      font-family: 'Fraunces', serif;
      font-variation-settings: "opsz" 144;
      font-weight: 600;
      font-size: 72px;
      line-height: 0.9;
      letter-spacing: -0.03em;
      color: var(--ink);
    }
    .streak-num .green { color: var(--forest); }
    .streak-label {
      margin-top: 6px;
      font-family: 'IBM Plex Mono', monospace;
      font-size: 10px;
      letter-spacing: 0.18em;
      text-transform: uppercase;
      color: var(--ink-soft);
    }
    .streak-sub {
      margin-top: 16px;
      padding-top: 14px;
      border-top: 1px dotted var(--rule);
      display: grid; grid-template-columns: 1fr 1fr; gap: 8px 16px;
      font-family: 'IBM Plex Mono', monospace;
      font-size: 11px;
      color: var(--ink-soft);
    }
    .streak-sub b {
      font-family: 'Fraunces', serif;
      font-weight: 500;
      font-style: italic;
      color: var(--ink);
      font-size: 13px;
    }
    .streak-checks {
      margin-top: 14px;
      display: flex; flex-wrap: wrap; gap: 5px;
    }
    .streak-checks .week {
      width: 22px; height: 22px;
      display: inline-flex; align-items: center; justify-content: center;
      font-family: 'IBM Plex Mono', monospace;
      font-size: 11px;
      border: 1px solid var(--rule);
      color: var(--ink-soft);
    }
    .streak-checks .week.green {
      background: var(--forest); color: var(--paper); border-color: var(--forest);
    }
    .streak-checks .week.amber {
      background: var(--ochre); color: var(--paper); border-color: var(--ochre);
    }
    .streak-checks .week.miss {
      background: repeating-linear-gradient(135deg,
        var(--paper) 0 3px, var(--rule) 3px 4px);
    }

    /* Form */
    .checkin-form .q {
      display: grid;
      grid-template-columns: 1fr auto;
      align-items: baseline;
      gap: 16px;
      padding: 10px 0;
      border-bottom: 1px dotted var(--rule);
      font-size: 14px;
    }
    .checkin-form .toggles {
      display: inline-flex; gap: 0;
      font-family: 'IBM Plex Mono', monospace;
      font-size: 11px;
      letter-spacing: 0.1em;
      text-transform: uppercase;
    }
    .checkin-form .toggles label {
      cursor: pointer;
      padding: 6px 14px;
      border: 1px solid var(--rule);
      border-right: none;
      background: var(--paper);
      color: var(--ink-soft);
      user-select: none;
    }
    .checkin-form .toggles label:last-of-type { border-right: 1px solid var(--rule); }
    .checkin-form .toggles input { display: none; }
    .checkin-form .toggles input[value="yes"]:checked + label,
    .checkin-form .toggles label:has(input[value="yes"]:checked) {
      background: var(--forest); color: var(--paper); border-color: var(--forest);
    }
    .checkin-form .toggles input[value="no"]:checked + label,
    .checkin-form .toggles label:has(input[value="no"]:checked) {
      background: var(--oxide); color: var(--paper); border-color: var(--oxide);
    }
    .checkin-form textarea {
      width: 100%;
      margin-top: 14px;
      padding: 12px 14px;
      background: var(--paper-deep);
      border: 1px solid var(--rule);
      font-family: 'IBM Plex Sans', sans-serif;
      font-size: 14px;
      line-height: 1.5;
      color: var(--ink);
      resize: vertical;
      min-height: 70px;
    }
    .checkin-form textarea:focus { outline: none; border-color: var(--ink); }
    .checkin-wrap button.submit {
      margin-top: 14px;
      padding: 12px 24px;
      background: var(--ink);
      color: var(--paper);
      border: 1px solid var(--ink);
      font-family: 'IBM Plex Mono', monospace;
      font-size: 11px;
      letter-spacing: 0.16em;
      text-transform: uppercase;
      cursor: pointer;
    }
    .checkin-wrap button.submit:hover { background: var(--oxide); border-color: var(--oxide); }
    .checkin-wrap button.submit:disabled { opacity: 0.4; cursor: not-allowed; }

    /* Verdict (after submit) */
    .verdict-block {
      padding: 18px 22px;
      background: var(--paper-deep);
      border-left: 3px solid var(--forest);
      font-family: 'IBM Plex Sans', sans-serif;
      font-size: 14px;
      line-height: 1.6;
    }
    .verdict-block.down-week { border-left-color: var(--oxide); }
    .verdict-block .head {
      display: flex; gap: 12px; align-items: baseline;
      margin-bottom: 10px;
      padding-bottom: 8px;
      border-bottom: 1px dotted var(--rule);
    }
    .verdict-block .call {
      font-family: 'Fraunces', serif;
      font-style: italic;
      font-weight: 500;
      font-size: 22px;
      letter-spacing: -0.01em;
    }
    .verdict-block .yes-count {
      margin-left: auto;
      font-family: 'IBM Plex Mono', monospace;
      font-size: 11px;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      color: var(--ink-soft);
    }

    /* Footer rule with coordinates */
    footer.coords {
      margin-top: 56px;
      padding-top: 18px;
      border-top: 1px solid var(--rule);
      display: flex; justify-content: space-between; align-items: center; gap: 24px;
      font-family: 'IBM Plex Mono', monospace;
      font-size: 10px;
      letter-spacing: 0.14em;
      text-transform: uppercase;
      color: var(--ink-soft);
    }
    .principles-link {
      color: var(--ink-soft);
      text-decoration: none;
      border-bottom: 1px dotted var(--rule);
      padding-bottom: 2px;
      transition: color 120ms ease, border-color 120ms ease;
    }
    .principles-link:hover {
      color: var(--oxide);
      border-bottom-color: var(--oxide);
    }
    @media (max-width: 720px) {
      footer.coords { flex-direction: column; gap: 12px; text-align: center; }
    }
  </style>
</head>
<body>
  <div class="shell">
    <header class="masthead">
      <div>
        <div class="brand">Two&nbsp;Oceans <em>&nbsp;·&nbsp;a&nbsp;log</em></div>
        <div class="tagline">Porsgrunn &nbsp;&rarr;&nbsp; Cape Town &nbsp;·&nbsp; 56 K</div>
      </div>
      <div class="stamp">
        <span class="big">{{ today_long }}</span>
        <span class="meta">wk {{ plan_week }} / 48 &nbsp;·&nbsp; {{ phase_short }} &nbsp;·&nbsp; tgt {{ target_h }}h</span>
        <div class="sync-row">
          <button class="refresh"
                  hx-post="/api/sync"
                  hx-target="#sync-result"
                  hx-swap="innerHTML"
                  hx-indicator="#sync-spin"
                  hx-disabled-elt="this"
                  hx-on::after-request="htmx.trigger('#today','refresh'); htmx.trigger('#readiness','refresh'); htmx.trigger('#hrv','refresh'); htmx.trigger('#rhr','refresh'); htmx.trigger('#sleep','refresh'); htmx.trigger('#stress','refresh'); htmx.trigger('#weight','refresh'); htmx.trigger('#volume','refresh'); htmx.trigger('#checkin','refresh')">
            ↻ pull &amp; refresh
          </button>
          <span id="sync-spin" class="htmx-indicator">syncing…</span>
          <span id="sync-result"
                hx-get="/api/sync/status" hx-trigger="load" hx-swap="innerHTML"></span>
        </div>
      </div>
    </header>

    <div class="journey">
      <div class="journey-line"></div>
      <div class="journey-fill" style="width: {{ journey_pct }}%"></div>
      {% for m in journey_markers %}
      <div class="journey-tick {{ m.kind }}" style="left: {{ m.pct }}%"></div>
      <div class="journey-label {{ m.kind }}" style="left: {{ m.pct }}%">{{ m.label }}</div>
      {% endfor %}
      <svg class="journey-curve" viewBox="0 0 100 14" preserveAspectRatio="none">
        <path d="{{ volume_path_filled }}" fill="rgba(200,54,45,0.12)" stroke="none"/>
        <path d="{{ volume_path_outline }}" fill="none"
              stroke="rgba(28,31,42,0.35)" stroke-width="0.6" stroke-dasharray="0.6 0.9"/>
        <path d="{{ volume_path_done }}" fill="none"
              stroke="var(--oxide)" stroke-width="1"/>
        <circle cx="{{ journey_pct }}" cy="{{ today_cy }}" r="1.6" fill="var(--oxide)"/>
      </svg>
      <div class="journey-curve-labels">
        <span class="lo">now {{ volume_now }}h/wk</span>
        <span class="hi">peak {{ volume_max }}h/wk</span>
      </div>
    </div>

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
        <div id="today" hx-get="/api/today" hx-trigger="load,refresh" hx-swap="innerHTML">
          <div class="text-zinc-500">loading…</div>
        </div>
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

      <section class="section span-3">
        <div id="checkin-or-auth"></div>
        <div class="section-head">
          <span class="roman">II<sup>b</sup>.</span>
          <h2 class="label">Sunday check-in &amp; streak</h2>
          <span class="section-meta">principles §6</span>
        </div>
        <div id="checkin" hx-get="/api/checkin" hx-trigger="load,refresh"
             hx-swap="innerHTML"></div>
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

      <section class="section data-card">
        <div class="section-head">
          <span class="roman">VI<sup>b</sup>.</span>
          <h2 class="label">Alcohol</h2>
          <span class="section-meta">30 d · vs HRV</span>
        </div>
        <div id="alcohol" hx-get="/api/alcohol" hx-trigger="load,refresh" hx-swap="innerHTML"></div>
      </section>

      <section class="section span-2 data-card">
        <div class="section-head">
          <span class="roman">VII.</span>
          <h2 class="label">Weight · journey to 75 kg</h2>
          <span class="section-meta">all-time</span>
        </div>
        <div id="weight" hx-get="/api/weight" hx-trigger="load,refresh" hx-swap="innerHTML"></div>
      </section>

      <section class="section data-card">
        <div class="section-head">
          <span class="roman">VIII.</span>
          <h2 class="label">Weekly volume</h2>
          <span class="section-meta">12 wk</span>
        </div>
        <div id="volume" hx-get="/api/volume" hx-trigger="load,refresh" hx-swap="innerHTML"></div>
      </section>
    </div>

    <footer class="coords">
      <span>59°08′N&nbsp;&nbsp;09°39′E&nbsp;&nbsp;Porsgrunn</span>
      <a class="principles-link"
         href="https://github.com/mmichelli/training/blob/main/principles.md"
         target="_blank" rel="noopener">
        principles · how &amp; why this plan will work →
      </a>
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
  <blockquote class="maxim">
    <span class="maxim-mark">¶</span>
    <span class="maxim-text">{{ maxim_text }}</span>
    <cite>— {{ maxim_attr }}</cite>
  </blockquote>
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


CHECKIN_PARTIAL = Template(r"""
<div class="checkin-wrap">
  <aside class="streak-tile">
    <div class="streak-num"><span class="green">{{ green_streak }}</span></div>
    <div class="streak-label">green weeks · in a row</div>
    <div class="streak-checks">
      {% for w in weeks_strip %}
      <span class="week {{ w.cls }}" title="{{ w.title }}">{{ w.glyph }}</span>
      {% endfor %}
    </div>
    <div class="streak-sub">
      <span>submitted<br><b>{{ checkin_streak }} wk</b></span>
      <span>this year<br><b>{{ total_green }} / {{ total_checkins }}</b></span>
    </div>
  </aside>
  <div>
    {% if today_record %}
      <div class="verdict-block {% if today_record.result == 'down-week' %}down-week{% endif %}">
        <div class="head">
          <span class="call">
            {% if today_record.result == 'down-week' %}Down-week ahead.
            {% else %}Continue as written.{% endif %}
          </span>
          <span class="yes-count">{{ today_record.yes_count }} / 5 yes · week ending {{ today_record.week_ending_eu }}</span>
        </div>
        <div>{{ today_record.ai_verdict }}</div>
        <div style="margin-top:14px;">
          <button class="submit"
                  hx-get="/api/checkin?reopen=1"
                  hx-target="#checkin" hx-swap="innerHTML">
            ↻ revise
          </button>
        </div>
      </div>
    {% elif not show_form %}
      <div class="verdict-block">
        <div class="head">
          <span class="call">No check-in yet for week ending {{ week_ending_eu }}.</span>
        </div>
        <div style="margin-top:14px;">
          <button class="submit"
                  hx-get="/api/checkin?reopen=1"
                  hx-target="#checkin" hx-swap="innerHTML">
            log check-in
          </button>
        </div>
      </div>
    {% else %}
      <form class="checkin-form"
            hx-post="/api/checkin" hx-target="#checkin" hx-swap="innerHTML">
        <input type="hidden" name="week_ending" value="{{ week_ending }}">
        {% for key, q in questions %}
        <div class="q">
          <span>{{ q }}</span>
          <span class="toggles">
            <label><input type="radio" name="{{ key }}" value="yes" required>yes</label>
            <label><input type="radio" name="{{ key }}" value="no">no</label>
          </span>
        </div>
        {% endfor %}
        <textarea name="notes" placeholder="anything else worth logging — knee, life stress, sleep nights, gut feel"></textarea>
        <button class="submit" type="submit">submit · evaluate</button>
      </form>
    {% endif %}
  </div>
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


FIELD_MAXIMS = [
    ("You don't get more by doing more — you get more by doing the right amount.",
     "Kristoffer Ingebrigtsen"),
    ("Trust the structure. The hard sessions show up as more reps, not faster reps.",
     "the plan"),
    ("The mountain decides.",
     "Kilian Jornet"),
    ("Every rep should end like you could have done another.",
     "NSA rule"),
    ("Discipline equals freedom.",
     "Jocko Willink"),
    ("Run by feel, train by numbers.",
     "Joe Friel"),
    ("If it scares you, walk.",
     "ultra adage"),
    ("The plan is the plan.",
     "the plan"),
    ("The best ability is availability.",
     "training-room cliché, true"),
    ("Walk with pride on the climbs. Run with intention on the rest.",
     "Constantia Nek strategy"),
    ("You are not training for a 56K race. You are training to be the kind of person who shows up consistently for a year.",
     "the plan"),
    ("Slow is smooth. Smooth is fast.",
     "old saying, still true"),
    ("There is no podium in week 4. Only consistency.",
     "the plan"),
]


def maxim_for_day(d: date) -> tuple[str, str]:
    return FIELD_MAXIMS[d.toordinal() % len(FIELD_MAXIMS)]


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
    # Journey: from PLAN_START → race day (Two Oceans). Tick each race + today.
    journey_start = PLAN_START
    journey_end = RACES[-1][0]
    journey_total = max((journey_end - journey_start).days, 1)
    elapsed = (today - journey_start).days
    journey_pct = max(0, min(100, elapsed / journey_total * 100))
    journey_markers = []
    for d, rank, name, when, a_race in RACES:
        pct = max(0, min(100, (d - journey_start).days / journey_total * 100))
        journey_markers.append({
            "pct": pct, "kind": "race",
            "label": name.split()[0] if not a_race else "Cape Town",
        })
    journey_markers.append({"pct": journey_pct, "kind": "today", "label": "today"})
    # Target weekly volume curve: paint the whole 48-week shape under the
    # timeline so Mario can see "where I am on the ramp."
    weeks = list(range(1, 49))
    vols = [WEEKLY_TOTAL.get(w, 0.0) for w in weeks]
    vmin, vmax = min(vols), max(vols)
    vrange = max(vmax - vmin, 0.001)
    cur_week = max(1, min(48, p.plan_week))

    # SVG viewBox is 0..100 (x) by 0..14 (y); higher y is lower on screen.
    def xy(week: int, hours: float) -> tuple[float, float]:
        x = (week - 1) / 47 * 100
        y = 13 - (hours - vmin) / vrange * 12
        return x, y

    points = [xy(w, v) for w, v in zip(weeks, vols)]
    full_path = "M " + " L ".join(f"{x:.2f},{y:.2f}" for x, y in points)
    filled_path = full_path + f" L 100,14 L 0,14 Z"

    done_points = [xy(w, v) for w, v in zip(weeks, vols) if w <= cur_week]
    done_path = "M " + " L ".join(f"{x:.2f},{y:.2f}" for x, y in done_points)

    _, today_y = xy(cur_week, WEEKLY_TOTAL.get(cur_week, 0))

    return PAGE.render(
        today_long=today.strftime("%A %d %b %Y").lower(),
        plan_week=p.plan_week,
        phase_short=phase_short.split(" + ")[0],
        target_h=f"{p.target_hours:.1f}",
        milestones=milestones,
        journey_pct=journey_pct,
        journey_markers=journey_markers,
        volume_path_filled=filled_path,
        volume_path_outline=full_path,
        volume_path_done=done_path,
        today_cy=f"{today_y:.2f}",
        volume_now=f"{WEEKLY_TOTAL.get(cur_week, 0):.1f}",
        volume_max=f"{vmax:.0f}",
        PAPER=PAPER, PAPER_DEEP=PAPER_DEEP, INK=INK, INK_SOFT=INK_SOFT,
        RULE=RULE, OXIDE=OXIDE, FOREST=FOREST, OCHRE=OCHRE,
    )


_AUTH_PROMPT = (
    ' <a hx-get="/api/garmin-auth" hx-target="body" hx-swap="beforeend"'
    ' style="color:var(--oxide);border-bottom:1px solid var(--oxide);'
    ' cursor:pointer;text-decoration:none;margin-left:6px;">⚿ authorize</a>'
)


def _render_sync_chip() -> str:
    last_ok = _SYNC_STATE["last_at"]
    last_status = _SYNC_STATE["last_status"]
    last_err = _SYNC_STATE.get("last_error", "")
    if last_status == "ok":
        return f'<span class="sync-result ok">last sync {last_ok}</span>'
    if last_status == "error":
        # Auth-flavored errors (401/Unauthorized/no token) → offer re-authorize
        looks_like_auth = any(
            kw in last_err.lower()
            for kw in ("401", "unauthor", "oauth", "no oauth1", "expired", "token")
        )
        suffix = f"last good: {last_ok}" if last_ok else "no successful sync yet"
        chip = (
            f'<span class="sync-result err" title="{last_err[:200]}">'
            f'sync failed · {suffix}'
            f'{_AUTH_PROMPT if looks_like_auth else ""}'
            f'</span>'
        )
        return chip
    return '<span class="sync-result idle">never synced this session</span>'


GARMIN_OAUTH_URL = (
    "https://sso.garmin.com/sso/embed"
    "?clientId=GCM_ANDROID_DARK"
    "&locale=en"
    "&id=gauth-widget&embedWidget=true"
    "&gauthHost=https%3A%2F%2Fsso.garmin.com%2Fsso"
    "&service=https%3A%2F%2Fconnectapi.garmin.com%2Foauth-service%2Foauth%2Fpreauthorized"
    "&source=https%3A%2F%2Fsso.garmin.com%2Fsso%2Fembed"
    "&redirectAfterAccountLoginUrl=https%3A%2F%2Fconnectapi.garmin.com%2Foauth-service%2Foauth%2Fpreauthorized"
    "&redirectAfterAccountCreationUrl=https%3A%2F%2Fconnectapi.garmin.com%2Foauth-service%2Foauth%2Fpreauthorized"
    "&consumeServiceTicket=false&generateExtraServiceTicket=true&generateNoServiceTicket=false"
)


def _auth_modal(inner_html: str) -> str:
    """Wrap a piece of HTML in a centered modal overlay."""
    return f"""
<div id="auth-modal-bg"
     style="position:fixed;inset:0;background:rgba(28,31,42,0.55);z-index:1000;
            display:flex;align-items:flex-start;justify-content:center;padding:8vh 16px;"
     onclick="if(event.target===this){{document.getElementById('auth-modal-bg').remove();}}">
  <div style="background:var(--paper);max-width:560px;width:100%;border:1px solid var(--ink);
              padding:28px 28px 24px;position:relative;box-shadow:0 30px 60px rgba(0,0,0,0.25);
              max-height:84vh;overflow-y:auto;">
    <button onclick="document.getElementById('auth-modal-bg').remove();"
            style="position:absolute;top:8px;right:12px;background:none;border:none;
                   font-size:22px;color:var(--ink-soft);cursor:pointer;line-height:1;">×</button>
    {inner_html}
  </div>
</div>
"""


@app.get("/api/garmin-auth", response_class=HTMLResponse)
async def api_garmin_auth():
    """Render the one-time OAuth bootstrap helper as a modal."""
    body = f"""
<h2 style="font-family:'Fraunces',serif;font-style:italic;font-weight:500;font-size:24px;margin:0 0 8px;">Authorize Garmin <span style='font-style:normal;font-family:"IBM Plex Mono",monospace;font-size:11px;color:var(--ink-soft);letter-spacing:0.16em;'>· ONE TIME</span></h2>
<p style="color:var(--ink-soft);margin:0 0 18px;font-size:13px;line-height:1.55;">
  Already logged into Garmin Connect in this browser? Tap the link below in a
  new tab, then copy the URL the tab lands on (it contains <code>ticket=ST-…</code>)
  and paste it back here.
</p>
<ol style="padding-left:20px;margin:0 0 18px;font-size:14px;line-height:1.7;">
  <li>
    <a href="{GARMIN_OAUTH_URL}" target="_blank" rel="noopener"
       style="color:var(--oxide);font-weight:500;border-bottom:1px solid var(--oxide);text-decoration:none;">
      Open Garmin OAuth URL ↗
    </a>
  </li>
  <li>After it loads, copy the full URL from the address bar.</li>
  <li>Paste it below and submit.</li>
</ol>
<form hx-post="/api/garmin-auth"
      hx-target="#auth-modal-bg" hx-swap="outerHTML"
      style="display:flex;gap:8px;">
  <input type="text" name="ticket_or_url" autofocus
         placeholder="paste ST-… or full URL"
         required
         style="flex:1;padding:10px 12px;background:var(--paper-deep);border:1px solid var(--rule);
                font-family:'IBM Plex Mono',monospace;font-size:12px;color:var(--ink);">
  <button type="submit"
          style="background:var(--ink);color:var(--paper);border:1px solid var(--ink);
                 padding:10px 18px;font-family:'IBM Plex Mono',monospace;font-size:11px;
                 letter-spacing:0.15em;text-transform:uppercase;cursor:pointer;">
    authorize
  </button>
</form>
"""
    return _auth_modal(body)


@app.post("/api/garmin-auth", response_class=HTMLResponse)
async def api_garmin_auth_submit(ticket_or_url: str = Form(...)):
    import garmin_oauth as G
    val = ticket_or_url.strip()
    if val.startswith("http"):
        from urllib.parse import urlparse, parse_qs
        q = parse_qs(urlparse(val).query)
        val = q.get("ticket", [""])[0]
    if not val.startswith("ST-"):
        return _auth_modal(
            f'<p style="color:var(--oxide);font-family:\'IBM Plex Mono\',monospace;'
            f'font-size:12px;">Not a valid Garmin ticket (must start with ST-): {val[:60]}</p>'
        )
    try:
        G.exchange_ticket_for_oauth1(val)
        G.fetch_oauth2()
    except Exception as e:
        return _auth_modal(
            f'<p style="color:var(--oxide);font-family:\'IBM Plex Mono\',monospace;'
            f'font-size:12px;line-height:1.6;">Exchange failed:<br>{str(e)[:300]}</p>'
        )
    return _auth_modal(
        '<div style="text-align:center;padding:8px 0;">'
        '<div style="font-family:\'Fraunces\',serif;font-style:italic;font-weight:500;'
        'font-size:28px;color:var(--forest);margin-bottom:8px;">✓ Authorized</div>'
        '<p style="color:var(--ink);font-size:14px;line-height:1.6;margin:0;">'
        'OAuth tokens saved — good for ~12 months. Close this and tap '
        '<strong>↻ pull &amp; refresh</strong> to test.</p>'
        '</div>'
    )


@app.post("/api/sync", response_class=HTMLResponse)
async def api_sync():
    _do_sync_blocking()
    return _render_sync_chip()


@app.get("/api/sync/status", response_class=HTMLResponse)
async def api_sync_status():
    return _render_sync_chip()


@app.get("/api/today", response_class=HTMLResponse)
async def api_today():
    today = date.today()
    p = prescription_for(today)
    text, attr = maxim_for_day(today)
    return TODAY_PARTIAL.render(
        weekday=p.weekday, title=p.title, purpose=p.purpose, description=p.description,
        maxim_text=text, maxim_attr=attr,
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


@app.get("/api/weight", response_class=HTMLResponse)
async def api_weight():
    df = load_weight()
    if df.empty:
        return CARD_WITH_STAT.render(stat_big="—", stat_unit="kg", chart=weight_chart())
    last = df.iloc[-1]
    to_goal = last["kg"] - WEIGHT_GOAL_KG
    # Trend vs 90d
    cutoff = last["date"] - pd.Timedelta(days=90)
    prior90 = df[df["date"] >= cutoff]
    delta_str = ""
    delta_dir = "flat"
    if len(prior90) >= 2:
        d90 = last["kg"] - prior90.iloc[0]["kg"]
        delta_dir = "down" if d90 <= 0 else "up"  # losing weight is good toward goal
        delta_str = f"{d90:+.1f} kg / 90d"
    return CARD_WITH_STAT.render(
        stat_big=f"{last['kg']:.1f}",
        stat_unit=f"kg · {to_goal:+.1f} kg to goal",
        delta=delta_str, delta_dir=delta_dir,
        chart=weight_chart(),
    )


_SYNC_STATE = {
    "last_status": "idle",
    "last_at": None,
    "last_attempt_at": None,
    "last_error": "",
    "last_summary": "",
}


def _do_sync_blocking() -> dict:
    """Refresh cookies then run ingest. Returns summary dict."""
    import subprocess, sys
    from datetime import datetime as _dt
    summary = {"refresh": "", "ingest": "", "ok": True}

    # Use the *same* interpreter the uvicorn process is running under — avoids
    # nested `uv run` confusion and PATH-inheritance issues.
    py = sys.executable

    def run(script: str, *args: str, timeout: int = 240) -> tuple[int, str]:
        try:
            r = subprocess.run(
                [py, script, *args],
                cwd=str(ROOT), capture_output=True, text=True, timeout=timeout,
            )
            output = (r.stdout or "") + (r.stderr or "")
            return r.returncode, output.strip()
        except Exception as e:
            return -1, f"failed: {e}"

    rc, out = run("refresh_session.py", timeout=60)
    if out:
        summary["refresh"] = out.splitlines()[-1]
    else:
        summary["refresh"] = "(no output)"
    if rc != 0:
        summary["ok"] = False

    rc, out = run("ingest.py", "--days", "14", timeout=240)
    if out:
        lines = [l for l in out.splitlines() if l.strip()]
        summary["ingest"] = " · ".join(lines[-4:])
    else:
        summary["ingest"] = "(no output)"
    if rc != 0:
        summary["ok"] = False

    now = _dt.now().strftime("%H:%M:%S")
    _SYNC_STATE["last_attempt_at"] = now
    if summary["ok"]:
        _SYNC_STATE["last_status"] = "ok"
        _SYNC_STATE["last_at"] = now
    else:
        _SYNC_STATE["last_status"] = "error"
        _SYNC_STATE["last_error"] = summary.get("ingest") or summary.get("refresh") or "unknown"
        # do NOT touch last_at — keep the last *successful* sync time
    _SYNC_STATE["last_summary"] = f"{summary['refresh']} · {summary['ingest']}"
    return summary


def _build_checkin_view(reopen: bool = False) -> str:
    today = date.today()
    week_ending = C.sunday_of(today)
    if week_ending > today:
        week_ending = C.previous_sunday(week_ending)
    existing = C.load(week_ending)
    s = C.streaks(today)

    # Build 12-week strip visualization, oldest left, newest right
    strip = []
    cursor = week_ending
    for _ in range(12):
        rec = C.load(cursor)
        if rec:
            if rec.get("yes_count", 0) >= 3:
                strip.append({"cls": "green", "glyph": "✓", "title": f"{eu_date(cursor)} · {rec['yes_count']}/5"})
            else:
                strip.append({"cls": "amber", "glyph": "△", "title": f"{eu_date(cursor)} · {rec['yes_count']}/5 → down-week"})
        else:
            strip.append({"cls": "miss", "glyph": " ", "title": f"{eu_date(cursor)} · no check-in"})
        cursor = C.previous_sunday(cursor)
    strip.reverse()

    return CHECKIN_PARTIAL.render(
        green_streak=s.green_streak,
        checkin_streak=s.checkin_streak,
        total_green=s.total_green,
        total_checkins=s.total_checkins,
        weeks_strip=strip,
        today_record=(
            {**existing, "week_ending_eu": eu_date(existing["week_ending"])}
            if existing and not reopen else None
        ),
        show_form=reopen,
        week_ending=week_ending.isoformat(),
        week_ending_eu=eu_date(week_ending),
        questions=C.QUESTIONS,
    )


@app.get("/api/checkin", response_class=HTMLResponse)
async def api_checkin_get(reopen: int = 0):
    return _build_checkin_view(reopen=bool(reopen))


@app.post("/api/checkin", response_class=HTMLResponse)
async def api_checkin_post(
    week_ending: str = Form(...),
    sleep_7h: str = Form("no"),
    knee_ok: str = Form("no"),
    long_run_room: str = Form("no"),
    both_gym_sessions: str = Form("no"),
    subthreshold_controlled: str = Form("no"),
    notes: str = Form(""),
):
    answers = {
        "sleep_7h": sleep_7h == "yes",
        "knee_ok": knee_ok == "yes",
        "long_run_room": long_run_room == "yes",
        "both_gym_sessions": both_gym_sessions == "yes",
        "subthreshold_controlled": subthreshold_controlled == "yes",
    }
    C.submit(date.fromisoformat(week_ending), answers, notes, signals_text())
    return _build_checkin_view()


ALCOHOL_PARTIAL = Template("""
<div class="stat-row">
  <span class="big">{{ last_units }}</span>
  <span class="unit">units · {{ last_label }}</span>
  <a hx-get="/api/alcohol?edit=1" hx-target="#alcohol" hx-swap="innerHTML"
     style="margin-left:auto;color:var(--oxide);border-bottom:1px solid var(--oxide);cursor:pointer;font-size:12px">log</a>
</div>
{% if insight %}<div style="color:var(--ink-soft);font-size:12px;margin-top:2px">{{ insight }}</div>{% endif %}
{{ chart|safe }}
""")


ALCOHOL_FORM_PARTIAL = Template("""
<form class="alcohol-form" hx-post="/api/alcohol" hx-target="#alcohol" hx-swap="innerHTML">
  <div style="display:flex;gap:8px;align-items:center;margin:4px 0 10px;flex-wrap:wrap">
    <input type="text" name="for_date" value="{{ default_date }}"
           pattern="[0-9]{2}/[0-9]{2}/[0-9]{4}" placeholder="DD/MM/YYYY"
           style="width:110px;padding:6px 8px;border:1px solid var(--rule);background:var(--paper);font:inherit;color:inherit"
           title="DD/MM/YYYY"/>
    <input type="number" name="units" min="0" step="0.5" value="{{ default_units }}" autofocus
           style="width:80px;padding:6px 8px;border:1px solid var(--rule);background:var(--paper);font:inherit;color:inherit"/>
    <span style="color:var(--ink-soft);font-size:12px">units</span>
    <button type="submit"
            style="padding:6px 12px;border:1px solid var(--ink);background:var(--ink);color:var(--paper);font:inherit;cursor:pointer">save</button>
    <a hx-get="/api/alcohol" hx-target="#alcohol" hx-swap="innerHTML"
       style="color:var(--ink-soft);cursor:pointer;font-size:12px">cancel</a>
    <span style="color:var(--ink-soft);font-size:12px;flex-basis:100%">1 unit ≈ 1 beer / 1 glass wine / 1 shot</span>
  </div>
</form>
{{ chart|safe }}
""")


def _units_for(df: pd.DataFrame, d: date) -> float:
    if df.empty:
        return 0.0
    m = df[df["date"] == pd.Timestamp(d)]
    return float(m.iloc[0]["units"]) if not m.empty else 0.0


@app.get("/api/alcohol", response_class=HTMLResponse)
async def api_alcohol_get(edit: int = 0):
    df = load_alcohol()
    today = date.today()
    yesterday = today - timedelta(days=1)
    if edit:
        return ALCOHOL_FORM_PARTIAL.render(
            default_date=eu_date(yesterday),
            today=today.isoformat(),
            default_units=f"{_units_for(df, yesterday):g}",
            chart=alcohol_chart(),
        )
    # Summary view: show most recent logged day (today if logged, else yesterday).
    if not df.empty and (df["date"] == pd.Timestamp(today)).any():
        last_units, last_label = _units_for(df, today), "today"
    else:
        last_units, last_label = _units_for(df, yesterday), "last night"
    return ALCOHOL_PARTIAL.render(
        last_units=f"{last_units:g}",
        last_label=last_label,
        insight=alcohol_hrv_insight(),
        chart=alcohol_chart(),
    )


def _parse_eu_or_iso(s: str) -> date:
    s = s.strip()
    if "/" in s:
        d, m, y = s.split("/")
        return date(int(y), int(m), int(d))
    return date.fromisoformat(s)


@app.post("/api/alcohol", response_class=HTMLResponse)
async def api_alcohol_post(units: float = Form(...), for_date: str = Form(...)):
    save_alcohol(_parse_eu_or_iso(for_date), units)
    return await api_alcohol_get()


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
