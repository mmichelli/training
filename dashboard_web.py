"""FastAPI + HTMX training dashboard — expedition logbook aesthetic.

Run:
    uv run uvicorn dashboard_web:app --reload --port 8765

Open http://localhost:8765.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
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
    #
    # `staticPlot: True` disables pan/zoom/hover. Critical on mobile — without
    # it, a vertical swipe over a chart captures the gesture and the page
    # won't scroll. We're read-only here (no interaction needed), so static
    # is the right call. `touch-action: pan-y` on the wrapper is belt-and-
    # braces in case Plotly's gesture handler still partially fires.
    body = fig.to_html(
        include_plotlyjs=False, div_id=div_id, full_html=False,
        config={"displayModeBar": False, "responsive": True, "staticPlot": True},
    )
    return f'<div style="touch-action: pan-y;">{body}</div>'


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
        fig.add_trace(go.Scatter(
            x=alc["date"], y=alc["units"], mode="lines+markers", name="units",
            line=dict(color=OXIDE, width=1.4),
            marker=dict(size=6, color=OXIDE, line=dict(color=PAPER, width=1)),
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
    html, body { overflow-x: hidden; }
    .shell {
      position: relative; z-index: 1;
      max-width: 1180px;
      margin: 0 auto;
      padding: 40px 48px 80px;
    }
    .shell, .shell * { box-sizing: border-box; }
    .section { min-width: 0; }
    .section * { max-width: 100%; }
    /* Plotly SVG occasionally renders wider than container on first paint */
    .plotly-graph-div, .plotly-graph-div svg { max-width: 100%; }
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
    .date-line {
      display: flex; align-items: center; gap: 12px;
    }
    .date-line .big { flex: 0 1 auto; }
    .sync-row {
      display: flex; flex-wrap: wrap; align-items: center; gap: 10px;
      margin-top: 8px;
    }
    .sync-status {
      font-family: 'IBM Plex Mono', monospace;
      font-size: 11px; letter-spacing: 0.04em;
      color: var(--ink-soft);
    }
    .refresh {
      background: none; cursor: pointer;
      font-family: 'IBM Plex Mono', monospace;
      font-size: 16px; line-height: 1;
      color: var(--ink-soft);
      width: 28px; height: 28px;
      display: inline-flex; align-items: center; justify-content: center;
      border: 1px solid var(--rule); border-radius: 50%;
      padding: 0;
      transition: color 120ms ease, border-color 120ms ease, transform 200ms ease;
    }
    .refresh:hover { color: var(--oxide); border-color: var(--oxide); }
    .refresh:active { transform: rotate(180deg); }
    .refresh:disabled { opacity: 0.4; cursor: progress; }
    .htmx-request .refresh > span { animation: spin 0.9s linear infinite; display: inline-block; }
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
      .stamp .date-line {
        grid-column: 1 / span 2;
        justify-content: space-between;
        margin-bottom: 4px;
      }
      .stamp .big {
        font-size: 18px;
      }
      .stamp .meta {
        grid-column: 1 / span 2;
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

    /* Section header — Fraunces editorial titles, strong dividers */
    .section {
      position: relative;
      padding-top: 4px;
    }
    .section-head {
      display: flex; align-items: baseline; gap: 16px;
      margin-bottom: 18px;
      padding-bottom: 10px;
      border-bottom: 1px solid var(--ink);
    }
    .roman {
      font-family: 'Fraunces', Georgia, serif;
      font-style: italic;
      font-variation-settings: "opsz" 60, "SOFT" 100;
      font-weight: 400;
      font-size: 22px;
      letter-spacing: -.005em;
      color: var(--oxide);
      line-height: 1;
      min-width: 36px;
    }
    h2.label {
      margin: 0;
      font-family: 'Fraunces', Georgia, serif;
      font-weight: 400;
      font-size: 22px;
      line-height: 1.05;
      letter-spacing: -.005em;
      color: var(--ink);
      font-variation-settings: "opsz" 60, "SOFT" 30;
    }
    .section-meta {
      margin-left: auto;
      font-family: 'IBM Plex Mono', monospace;
      font-size: 9px;
      letter-spacing: 0.18em;
      color: var(--ink-soft);
      text-transform: uppercase;
      text-align: right;
    }
    @media (max-width: 720px) {
      .grid { gap: 36px; }
      .section { padding-top: 0; }
      .section-head { gap: 12px; margin-bottom: 14px; padding-bottom: 8px; }
      .roman { font-size: 20px; min-width: 30px; }
      h2.label { font-size: 20px; line-height: 1.1; }
      .section-meta { display: none; }
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

    /* Journey — just the target-volume ramp curve. The race timeline that
       used to sit above it was duplicating the milestones strip below. */
    .journey {
      position: relative;
      margin: 24px 0 28px;
      padding: 0 4px;
      height: 44px;
    }

    /* Past-session dots: their own trail strip, above the ramp curve. */
    .session-trail {
      display: flex; align-items: center; gap: 12px;
      margin: 12px 0 8px;
    }
    .session-trail-label {
      font-family: 'IBM Plex Mono', monospace;
      font-size: 9px; letter-spacing: .18em; text-transform: uppercase;
      color: var(--ink-soft);
      white-space: nowrap;
    }
    .session-trail-line {
      position: relative; flex: 1;
      height: 12px;
    }
    .session-trail-line::before {
      content: ""; position: absolute; left: 0; right: 0; top: 50%;
      height: 1px; background: var(--rule);
      transform: translateY(-50%);
    }
    .session-dot {
      position: absolute;
      top: 50%;
      width: 7px; height: 7px;
      border-radius: 50%;
      background: var(--ink-soft);
      transform: translate(-50%, -50%);
      z-index: 2;
      cursor: help;
      transition: transform 100ms ease;
      box-shadow: 0 0 0 2px var(--paper);
    }
    .session-dot:hover { transform: translate(-50%, -50%) scale(1.6); z-index: 4; }
    .session-dot.run      { background: var(--oxide); }
    .session-dot.long-run {
      width: 10px; height: 10px;
      background: var(--oxide);
    }
    .session-dot.walk     { background: var(--ink-soft); }
    .session-dot.gym      { background: var(--ochre); }
    .session-dot.bike     { background: var(--forest); }
    .session-dot.other    { background: var(--ink); opacity: 0.55; }

    .journey-curve {
      position: absolute; top: 0; left: 0; right: 0;
      width: 100%; height: 28px;
      overflow: visible;
    }
    .journey-curve-labels {
      position: absolute; top: 32px; left: 0; right: 0;
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
      margin: 4px 0 40px;
    }
    @media (max-width: 980px) {
      .milestones { grid-template-columns: 1fr 1fr; }
    }
    @media (max-width: 720px) {
      /* Compact 4-chip row on phones — races shouldn't dominate the fold */
      .milestones { grid-template-columns: repeat(4, 1fr); margin: 0 0 22px; }
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
    @media (max-width: 720px) {
      .milestone {
        padding: 9px 6px 10px;
        grid-template-areas: "rank" "name" "countdown";
        grid-template-columns: 1fr;
        gap: 1px;
        text-align: center;
        border-right: 1px solid var(--rule);
        border-bottom: none;
      }
      .milestone:nth-child(2n) { border-right: 1px solid var(--rule); }
      .milestone:last-child { border-right: none; }
      .milestone .m-when { display: none; }
      .milestone .m-name {
        font-size: 11.5px; line-height: 1.1; letter-spacing: -.005em;
      }
      .milestone .m-rank { font-size: 11px; }
      .milestone .m-countdown { font-size: 13px; margin-top: 1px; }
      .milestone .m-countdown small { font-size: 9px; letter-spacing: .12em; }
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

    /* ─── §7 Activity Log — synced sessions, zones inline ───────────── */
    .activity-log-wrap { overflow-x: auto; }
    .activity-log { width: 100%; border-collapse: collapse; font-family: 'IBM Plex Mono', monospace; }
    .activity-log th, .activity-log td {
      text-align: left; padding: 10px 8px;
      border-bottom: 1px dotted var(--rule);
      font-size: 12px; vertical-align: baseline; white-space: nowrap;
    }
    .activity-log th {
      font-size: 9px; letter-spacing: .22em; text-transform: uppercase;
      color: var(--ink-soft); font-weight: 500;
      border-bottom: 1px solid var(--ink);
    }
    .activity-log td.al-day em {
      font-family: 'Fraunces', Georgia, serif; font-style: italic;
      color: var(--ink-soft); font-size: 13px; margin-right: 4px;
    }
    .activity-log td.al-name { white-space: normal; }
    .activity-log td.al-name strong {
      font-family: 'Fraunces', Georgia, serif; font-weight: 500;
      font-size: 13px; letter-spacing: -.005em;
    }
    .activity-log td.al-name em {
      display: block;
      font-family: 'Fraunces', Georgia, serif; font-style: italic;
      font-size: 11px; color: var(--ink-soft); margin-top: 1px;
    }
    .activity-log .al-dot {
      display: inline-block; width: 7px; height: 7px; border-radius: 50%;
      margin-right: 6px; vertical-align: middle; background: var(--ink-soft);
    }
    .activity-log .al-dot.run { background: var(--oxide); }
    .activity-log .al-dot.long-run { background: var(--oxide);
      box-shadow: 0 0 0 1.5px var(--paper-deep); }
    .activity-log .al-dot.walk { background: var(--ink-soft); }
    .activity-log .al-dot.gym { background: var(--ochre); }
    .activity-log .al-dot.bike { background: var(--forest); }
    .activity-log .muted { color: var(--ink-soft); }
    .activity-log .al-zones .z {
      display: inline-block; padding: 1px 5px; margin-right: 3px;
      border: 1px solid var(--rule); font-size: 10px; letter-spacing: 0;
    }
    .activity-log .al-zones .z.dom {
      border-color: var(--ink); color: var(--ink); font-weight: 600;
    }

    /* ─── §2 Coming Week — compact, clickable session list ──────────── */
    .coming-week { margin: 32px 0; }
    .coming-week-head {
      display: flex; flex-wrap: wrap; align-items: baseline;
      gap: 14px;
      padding-bottom: 10px;
      border-bottom: 1px solid var(--ink);
    }
    .coming-week-head .roman {
      color: var(--oxide); font-style: italic;
      font-family: 'Fraunces', Georgia, serif;
      font-variation-settings: "opsz" 60, "SOFT" 100;
      font-weight: 400; font-size: 22px; line-height: 1;
    }
    .coming-week-head h2 {
      font-family: 'Fraunces', Georgia, serif;
      font-variation-settings: "opsz" 60, "SOFT" 30;
      font-weight: 400; font-size: 22px; line-height: 1; letter-spacing: -.005em;
      flex: 1 1 auto;
    }
    .coming-week-head .meta {
      font-family: 'IBM Plex Mono', monospace;
      font-size: 10px; letter-spacing: .18em; text-transform: uppercase; color: var(--ink-soft);
      flex: 0 0 auto;
    }
    .cw-maxim {
      margin: 12px 0 4px;
      font-family: 'Fraunces', Georgia, serif; font-style: italic;
      font-size: 14px; color: var(--ink-soft); line-height: 1.55;
      max-width: 64ch;
    }
    .cw-maxim-mark { color: var(--oxide); margin-right: 6px; font-weight: 600; }
    .cw-maxim cite {
      font-family: 'IBM Plex Mono', monospace; font-style: normal;
      font-size: 10px; letter-spacing: .14em; text-transform: uppercase;
      color: var(--ink-soft); margin-left: 8px;
    }
    .week-list { margin-top: 6px; }
    .week-row {
      display: grid; grid-template-columns: 64px 1fr auto; align-items: center;
      gap: 18px; padding: 11px 4px 11px 16px;
      border-bottom: 1px dotted var(--rule);
      cursor: pointer; position: relative;
      transition: background 120ms ease;
    }
    .week-row:last-child { border-bottom: none; }
    .week-row:hover { background: rgba(28,31,42,.025); }
    .week-row.is-today { background: rgba(200,54,45,.05); }
    .week-row.is-today::before {
      content: ""; position: absolute; left: -2px; top: 50%;
      width: 6px; height: 6px; border-radius: 50%;
      background: var(--oxide); transform: translateY(-50%);
      box-shadow: 0 0 0 3px var(--paper), 0 0 0 4px var(--oxide);
    }
    .week-row.rest {
      background: repeating-linear-gradient(135deg, transparent 0 6px, rgba(155,148,132,.06) 6px 7px);
    }
    .week-row .wr-day { display: flex; flex-direction: column; line-height: 1; }
    .week-row .wr-day strong {
      font-family: 'Fraunces', Georgia, serif; font-style: italic; font-weight: 400;
      font-size: 17px; color: var(--ink);
    }
    .week-row .wr-day small {
      font-family: 'IBM Plex Mono', monospace; font-size: 10px;
      letter-spacing: .10em; color: var(--ink-soft); margin-top: 3px;
    }
    .week-row.is-today .wr-day small { color: var(--oxide); font-weight: 600; }
    .week-row .wr-session { font-size: 13px; line-height: 1.35; color: var(--ink); }
    .week-row .wr-session strong {
      font-family: 'Fraunces', Georgia, serif; font-weight: 500;
      font-size: 15px; letter-spacing: -.005em;
    }
    .week-row .wr-session em {
      font-family: 'Fraunces', Georgia, serif; font-style: italic;
      font-size: 12px; color: var(--ink-soft); margin-left: 8px;
    }
    .week-row .wr-tag {
      font-family: 'IBM Plex Mono', monospace;
      font-size: 9px; letter-spacing: .2em; text-transform: uppercase; color: var(--ink-soft);
      padding: 1px 6px; border: 1px solid var(--rule); margin-left: 6px;
    }
    .week-row.is-today .wr-tag { color: var(--oxide); border-color: var(--oxide); }
    .week-row .wr-toggle {
      font-family: 'IBM Plex Mono', monospace;
      font-size: 10px; letter-spacing: .18em; text-transform: uppercase; color: var(--ink-soft);
      user-select: none;
    }
    .week-row .wr-toggle::after {
      content: " ▾"; transition: transform 150ms ease; display: inline-block;
    }
    .week-row.open .wr-toggle::after { transform: rotate(180deg); }
    .week-row .wr-expand {
      grid-column: 1 / -1; display: none;
      padding: 6px 12px 14px 80px;
      border-top: 1px dashed var(--rule);
      margin-top: 10px;
    }
    .week-row.open .wr-expand { display: block; }
    .week-row .wr-expand p {
      font-size: 13px; line-height: 1.6; color: var(--ink);
      max-width: 60ch; white-space: pre-wrap; margin-bottom: 6px;
    }
    @media (max-width: 720px) {
      .coming-week-head .meta { flex-basis: 100%; margin-top: 4px; }
      .week-row { grid-template-columns: 52px 1fr auto; gap: 10px; padding: 9px 0 9px 12px; }
      .week-row .wr-day strong { font-size: 15px; }
      .week-row .wr-day small { white-space: nowrap; }
      .week-row .wr-session em { display: block; margin-left: 0; margin-top: 2px; }
      .week-row .wr-tag { margin-left: 0; margin-top: 4px; display: inline-block; }
      .week-row .wr-expand { padding-left: 56px; }
      .week-row .wr-toggle { font-size: 9px; letter-spacing: .12em; }
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
        <div class="date-line">
          <span class="big">{{ today_long }}</span>
          <button class="refresh"
                  title="pull &amp; refresh"
                  aria-label="pull and refresh"
                  hx-post="/api/sync"
                  hx-target="#sync-result"
                  hx-swap="innerHTML"
                  hx-indicator="this"
                  hx-disabled-elt="this"
                  hx-on::before-request="document.getElementById('sync-result').textContent='syncing…'"
                  hx-on::after-request="htmx.trigger('#readiness','refresh'); htmx.trigger('#trend-body','refresh'); htmx.trigger('#weight','refresh'); htmx.trigger('#volume','refresh'); htmx.trigger('#checkin','refresh'); htmx.trigger('#log','refresh'); htmx.trigger('#tasks','refresh'); htmx.trigger('#activity-log','refresh')">
            <span>↻</span>
          </button>
        </div>
        <span class="meta">wk {{ plan_week }} / 48 &nbsp;·&nbsp; {{ phase_short }} &nbsp;·&nbsp; tgt {{ target_h }}h</span>
        <span id="sync-result" class="sync-status"
              hx-get="/api/sync/status" hx-trigger="load" hx-swap="innerHTML"></span>
      </div>
    </header>

    {% if session_dots %}
    <div class="session-trail" aria-label="trail of completed sessions">
      <span class="session-trail-label">done so far</span>
      <div class="session-trail-line">
        {% for d in session_dots %}
        <span class="session-dot {{ d.cls }}" style="left: {{ d.pct }}%" title="{{ d.title }}"></span>
        {% endfor %}
      </div>
    </div>
    {% endif %}

    <div class="journey">
      <svg class="journey-curve" viewBox="0 0 100 14" preserveAspectRatio="none">
        <path d="{{ volume_path_filled }}" fill="rgba(200,54,45,0.12)" stroke="none"/>
        <path d="{{ volume_path_outline }}" fill="none"
              stroke="rgba(28,31,42,0.35)" stroke-width="0.6" stroke-dasharray="0.6 0.9"/>
        <path d="{{ volume_path_done }}" fill="none"
              stroke="var(--oxide)" stroke-width="1"/>
        <circle cx="{{ journey_pct }}" cy="{{ today_cy }}" r="1.6" fill="var(--oxide)"/>
      </svg>
      <div class="journey-curve-labels">
        <span class="lo">now {{ volume_now }} h/wk</span>
        <span class="hi">peak {{ volume_max }} h/wk</span>
      </div>
    </div>

    <nav class="milestones">
      {% for m in milestones %}
      <a class="milestone {% if m.done %}done{% endif %} {% if m.a_race %}a-race{% endif %}"
         href="{{ m.url }}" target="_blank" rel="noopener noreferrer">
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

    <div id="calibration" hx-get="/api/calibration" hx-trigger="load"></div>

    <section class="coming-week">
      <div class="coming-week-head">
        <span class="roman">§ 2</span>
        <h2>The Coming Week</h2>
        <span class="meta">wk {{ plan_week }} · tgt {{ target_h }} h · {{ week_actual_h }} h logged</span>
      </div>
      <p class="cw-maxim">
        <span class="cw-maxim-mark">¶</span>
        {{ maxim_text }}
        <cite>— {{ maxim_attr }}</cite>
      </p>
      <div class="week-list">
        {% for d in coming_days %}
        <article class="week-row {% if d.is_today %}is-today{% endif %} {% if d.is_rest %}rest{% endif %}"
                 onclick="if(!event.target.closest('button,a')) this.classList.toggle('open')">
          <div class="wr-day">
            <strong>{{ d.weekday }}</strong><small>{{ d.daynum }}</small>
          </div>
          <div class="wr-session">
            <strong>{{ d.title }}</strong>
            {% if d.purpose %}<em>{{ d.purpose|lower }}</em>{% endif %}
            {% if d.is_today %}<span class="wr-tag">today</span>{% endif %}
            {% if d.is_rest and not d.is_today %}<span class="wr-tag">rest</span>{% endif %}
          </div>
          <div class="wr-toggle">{% if d.is_rest and not d.description %}—{% else %}open{% endif %}</div>
          <div class="wr-expand">
            <p>{{ d.description }}</p>
          </div>
        </article>
        {% endfor %}
      </div>
    </section>

    <div class="grid">
      <section class="section span-2 data-card">
        <div class="section-head">
          <span class="roman">§ 7</span>
          <h2 class="label">The Log</h2>
          <span class="section-meta">synced sessions · last 14 d · zones inline</span>
        </div>
        <div id="activity-log" hx-get="/api/activity-log" hx-trigger="load,refresh"
             hx-swap="innerHTML"></div>
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

      <section class="section span-3 data-card">
        <div class="section-head">
          <span class="roman">II<sup>c</sup>.</span>
          <h2 class="label">Operational tasks</h2>
          <span class="section-meta">tasks.yaml · calendar + dashboard</span>
        </div>
        <div id="tasks" hx-get="/api/tasks" hx-trigger="load,refresh"
             hx-swap="innerHTML"></div>
      </section>

      <section class="section span-3 data-card">
        <div class="section-head">
          <span class="roman">II<sup>d</sup>.</span>
          <h2 class="label">Today's log · protein · alcohol · knee</h2>
          <span class="section-meta">field journal · stamp daily</span>
        </div>
        <div id="log" hx-get="/api/log" hx-trigger="load,refresh" hx-swap="innerHTML"></div>
      </section>

      <section class="section span-2 data-card">
        <div class="section-head">
          <span class="roman">III.</span>
          <h2 class="label">Weekly volume · plan vs actual</h2>
          <span class="section-meta">12 wk</span>
        </div>
        <div id="volume" hx-get="/api/volume" hx-trigger="load,refresh" hx-swap="innerHTML"></div>
      </section>

      <section class="section span-2 data-card">
        <div class="section-head">
          <span class="roman">IV.</span>
          <h2 class="label">Weight · journey to 75 kg</h2>
          <span class="section-meta">all-time</span>
        </div>
        <div id="weight" hx-get="/api/weight" hx-trigger="load,refresh" hx-swap="innerHTML"></div>
      </section>

      <section class="section span-3 data-card">
        <div class="section-head">
          <span class="roman">V.</span>
          <h2 class="label">Trends · 60 d</h2>
          <span class="section-meta">tap to switch</span>
        </div>
        <style>
          .trend-tabs { display: flex; flex-wrap: wrap; gap: 0;
                        margin: 0 0 0.6rem 0;
                        border-bottom: 1px solid rgba(28,31,42,0.18); }
          .trend-tab {
            font-family: 'IBM Plex Mono', monospace; font-size: 0.78rem;
            letter-spacing: 0.12em; text-transform: uppercase;
            padding: 0.5rem 0.75rem 0.55rem; background: transparent;
            border: 0; border-bottom: 2px solid transparent;
            color: var(--ink-soft); cursor: pointer;
            margin-bottom: -1px;
          }
          .trend-tab:hover { color: var(--ink); }
          .trend-tab.active {
            color: var(--ink); border-bottom-color: var(--ink);
            font-weight: 600;
          }
        </style>
        <nav class="trend-tabs" role="tablist"
             onclick="if(event.target.matches('.trend-tab')){this.querySelectorAll('.trend-tab').forEach(b=>b.classList.remove('active'));event.target.classList.add('active');}">
          <button class="trend-tab active"
                  hx-get="/api/hrv" hx-target="#trend-body" hx-swap="innerHTML">HRV</button>
          <button class="trend-tab"
                  hx-get="/api/rhr" hx-target="#trend-body" hx-swap="innerHTML">RHR</button>
          <button class="trend-tab"
                  hx-get="/api/sleep" hx-target="#trend-body" hx-swap="innerHTML">Sleep</button>
          <button class="trend-tab"
                  hx-get="/api/stress" hx-target="#trend-body" hx-swap="innerHTML">Stress</button>
        </nav>
        <div id="trend-body" hx-get="/api/hrv" hx-trigger="load" hx-swap="innerHTML"></div>
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
    (date(2026, 7, 12), "i.",   "Porsgrunn parkrun",  "5 K benchmark",
     False, "https://www.parkrun.no/porsgrunn/"),
    (date(2026, 9, 12), "ii.",  "Oslo Half",          "21 K · fitness check",
     False, "https://oslomaraton.no/en/"),
    (date(2027, 2, 14), "iii.", "Sevilla Marathon",   "42 K · qualifier",
     False, "https://www.zurichmaratondesevilla.es/en/"),
    (date(2027, 4, 3),  "iv.",  "Two Oceans Ultra",   "56 K · A-race",
     True,  "https://www.twooceansmarathon.org.za/"),
]


@app.get("/", response_class=HTMLResponse)
async def index():
    today = date.today()
    p = prescription_for(today)
    phase_short = p.phase.split("·")[-1].strip() if "·" in p.phase else p.phase
    milestones = []
    for d, rank, name, when, a_race, url in RACES:
        days = (d - today).days
        milestones.append({
            "rank": rank, "name": name, "when": when,
            "days": days if days >= 0 else 0,
            "done": days < 0,
            "a_race": a_race,
            "url": url,
        })
    # Journey: from PLAN_START → race day (Two Oceans). Tick each race + today.
    journey_start = PLAN_START
    journey_end = RACES[-1][0]
    journey_total = max((journey_end - journey_start).days, 1)
    elapsed = (today - journey_start).days
    journey_pct = max(0, min(100, elapsed / journey_total * 100))
    journey_markers = []
    for d, rank, name, when, a_race, _url in RACES:
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

    # Scale the trail to elapsed-so-far (not full journey) so dots in the
    # current week don't pile on top of each other on a 11-month axis.
    # Allow 10% right-padding so dots near today aren't crammed at the edge.
    trail_span = max(elapsed * 1.10, 14)  # at least 14 days so a single dot doesn't fill the strip
    session_dots = _load_session_dots(journey_start, trail_span)

    # §2 Coming Week — today + next 6 days from plan_lookup
    coming_days = []
    for offset in range(7):
        d = today + timedelta(days=offset)
        pr = prescription_for(d)
        title = pr.title
        is_rest = title.lower().startswith("rest")
        coming_days.append({
            "weekday": d.strftime("%a"),
            "daynum": d.strftime("%d/%m"),
            "is_today": offset == 0,
            "is_rest": is_rest,
            "title": title,
            "purpose": pr.purpose if pr.purpose and pr.purpose.lower() != "recovery" else "",
            "description": pr.description,
        })

    # Hours logged in the current Mon–Sun week (for the "X h logged" meta line)
    week_actual_s = 0.0
    act_dir = ROOT / "activities"
    if act_dir.exists():
        wk_start = today - timedelta(days=today.weekday())
        for ap in act_dir.glob("*.md"):
            fm = _parse_activity_frontmatter(ap)
            if not fm or not fm.get("date"):
                continue
            try:
                ad = datetime.strptime(fm["date"], "%Y-%m-%d").date()
                if wk_start <= ad <= today:
                    week_actual_s += int(fm.get("duration_s") or 0)
            except ValueError:
                continue
    week_actual_h = f"{week_actual_s / 3600:.1f}"

    maxim_text, maxim_attr = maxim_for_day(today)

    return PAGE.render(
        today_long=today.strftime("%A %d %b %Y").lower(),
        plan_week=p.plan_week,
        phase_short=phase_short.split(" + ")[0],
        target_h=f"{p.target_hours:.1f}",
        milestones=milestones,
        journey_pct=journey_pct,
        journey_markers=journey_markers,
        session_dots=session_dots,
        coming_days=coming_days,
        week_actual_h=week_actual_h,
        maxim_text=maxim_text,
        maxim_attr=maxim_attr,
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


def _parse_activity_frontmatter(p: Path) -> dict[str, str] | None:
    """Read an activities/*.md file and return its frontmatter dict, or None."""
    try:
        head = p.read_text(errors="ignore").split("---", 2)
        if len(head) < 3:
            return None
        fm: dict[str, str] = {}
        for line in head[1].strip().splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                fm[k.strip()] = v.strip().strip('"')
        return fm
    except Exception:
        return None


def _load_session_dots(journey_start: date, journey_total_days: float) -> list[dict]:
    """Past sessions as dots on the journey line: one per completed activity."""
    act_dir = ROOT / "activities"
    if not act_dir.exists() or journey_total_days <= 0:
        return []
    dots: list[dict] = []
    for p in sorted(act_dir.glob("*.md")):
        fm = _parse_activity_frontmatter(p)
        if not fm or not fm.get("date"):
            continue
        try:
            d = datetime.strptime(fm["date"], "%Y-%m-%d").date()
            elapsed = (d - journey_start).days
            if elapsed < 0:
                continue
            pct = min(100.0, elapsed / journey_total_days * 100)
            atype = (fm.get("type") or "").lower()
            duration_h = int(fm.get("duration_s") or 0) / 3600
            distance_km = float(fm.get("distance_km") or 0)
            name = fm.get("name") or atype
            if atype.startswith("running") and duration_h >= 1.5:
                cls = "long-run"
            elif atype.startswith("running"):
                cls = "run"
            elif atype.startswith("walking") or atype.startswith("hiking"):
                cls = "walk"
            elif "strength" in atype or "weight" in atype:
                cls = "gym"
            elif atype.startswith("cycling") or atype.startswith("indoor_cycling"):
                cls = "bike"
            else:
                cls = "other"
            dots.append({
                "pct": pct,
                "cls": cls,
                "title": (
                    f"{d.strftime('%a %d %b')} · {name} · "
                    f"{distance_km:.1f} km · {int(duration_h * 60)} min"
                ),
            })
        except Exception:
            continue
    return dots


def _latest_activity_summary() -> str:
    """Return 'DD Mon HH:MM · <name>' for the most recent synced activity, or ''."""
    act_dir = ROOT / "activities"
    if not act_dir.exists():
        return ""
    latest_start = ""
    latest_name = ""
    for p in act_dir.glob("*.md"):
        fm = _parse_activity_frontmatter(p)
        if not fm:
            continue
        start = fm.get("start", "")
        if start and start > latest_start:
            latest_start = start
            latest_name = fm.get("name", "")
    if not latest_start:
        return ""
    try:
        dt = datetime.strptime(latest_start[:19], "%Y-%m-%dT%H:%M:%S")
        return f"{dt.strftime('%d %b %H:%M')} · {latest_name}".strip(" ·")
    except ValueError:
        return latest_name


def _render_sync_chip() -> str:
    last_ok = _SYNC_STATE["last_at"]
    last_status = _SYNC_STATE["last_status"]
    last_err = _SYNC_STATE.get("last_error", "")
    if last_status == "ok":
        latest = _latest_activity_summary()
        suffix = f" · latest {latest}" if latest else ""
        return f'<span class="sync-result ok">✓ synced {last_ok}{suffix}</span>'
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
    return ''


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


@app.get("/preview", response_class=HTMLResponse)
async def preview():
    """Serve the redesign sketch verbatim — static file, no data wiring."""
    return (ROOT / "dashboard_redesign.html").read_text()


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


def _activity_type_class(atype: str, duration_h: float) -> str:
    atype = (atype or "").lower()
    if atype.startswith("running") and duration_h >= 1.5:
        return "long-run"
    if atype.startswith("running"):
        return "run"
    if atype.startswith("walking") or atype.startswith("hiking"):
        return "walk"
    if "strength" in atype or "weight" in atype:
        return "gym"
    if atype.startswith("cycling") or atype.startswith("indoor_cycling"):
        return "bike"
    return "other"


def _load_recent_activities(days: int = 14) -> list[dict]:
    """Read activities/*.md (last N days), parse frontmatter, return rows for §7 Log."""
    act_dir = ROOT / "activities"
    if not act_dir.exists():
        return []
    cutoff = date.today() - timedelta(days=days)
    rows: list[dict] = []
    for p in sorted(act_dir.glob("*.md"), reverse=True):
        fm = _parse_activity_frontmatter(p)
        if not fm or not fm.get("date"):
            continue
        try:
            ad = datetime.strptime(fm["date"], "%Y-%m-%d").date()
            if ad < cutoff:
                continue
            dist_km = float(fm.get("distance_km") or 0)
            dur_s = int(fm.get("duration_s") or 0)
            moving_s = int(fm.get("moving_s") or dur_s)
            pace_s = int(moving_s / dist_km) if dist_km else 0
            zones = [int(fm.get(f"hr_z{n}_s") or 0) for n in range(1, 6)]
            ztotal = sum(zones) or 1
            zpct = [z / ztotal * 100 for z in zones]
            dom_zone = zpct.index(max(zpct)) + 1 if any(zones) else 0
            rows.append({
                "date": ad,
                "weekday": ad.strftime("%a"),
                "datestr": ad.strftime("%d %b"),
                "atype": fm.get("type", ""),
                "name": fm.get("name", ""),
                "cls": _activity_type_class(fm.get("type", ""), dur_s / 3600),
                "distance_km": dist_km,
                "duration_s": dur_s,
                "moving_s": moving_s,
                "pace_s": pace_s,
                "avg_hr": int(float(fm["avg_hr"])) if fm.get("avg_hr") else None,
                "max_hr": int(float(fm["max_hr"])) if fm.get("max_hr") else None,
                "zones_pct": zpct,
                "dom_zone": dom_zone,
                "has_zones": any(zones),
            })
        except Exception:
            continue
    return rows


ACTIVITY_LOG_PARTIAL = Template("""
{% if rows %}
<div class="activity-log-wrap">
  <table class="activity-log">
    <thead>
      <tr>
        <th>Day</th><th>Session</th><th>Dist</th><th>Time</th><th>Pace</th>
        <th>HR avg · max</th><th>Zones (% time)</th>
      </tr>
    </thead>
    <tbody>
      {% for r in rows %}
      <tr>
        <td class="al-day"><em>{{ r.weekday }}</em> {{ r.datestr }}</td>
        <td class="al-name">
          <span class="al-dot {{ r.cls }}"></span>
          <strong>{{ r.name }}</strong>
          <em>{{ r.atype }}</em>
        </td>
        <td>{{ "%.2f"|format(r.distance_km) }} km</td>
        <td>{{ "{:d}:{:02d}".format(r.duration_s // 60, r.duration_s % 60) }}</td>
        <td>
          {% if r.pace_s %}
            {{ "{:d}:{:02d}".format(r.pace_s // 60, r.pace_s % 60) }}<span class="muted">/km</span>
          {% else %}—{% endif %}
        </td>
        <td>{% if r.avg_hr %}{{ r.avg_hr }} · {{ r.max_hr or "—" }}{% else %}—{% endif %}</td>
        <td class="al-zones">
          {% if r.has_zones %}
            {% for pct in r.zones_pct %}
              {% set n = loop.index %}
              <span class="z {% if n == r.dom_zone %}dom{% endif %}">Z{{ n }} {{ "%.0f"|format(pct) }}%</span>
            {% endfor %}
          {% else %}<span class="muted">no zone data</span>{% endif %}
        </td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</div>
{% else %}
<div class="empty">no activities synced yet</div>
{% endif %}
""")


@app.get("/api/activity-log", response_class=HTMLResponse)
async def api_activity_log():
    return ACTIVITY_LOG_PARTIAL.render(rows=_load_recent_activities(14))


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


# ─────────── tasks panel ───────────

import tasks as _tasks


_TASK_ROW_CSS = """
<style>
  .tasks-list { list-style: none; padding: 0; margin: 0; }
  .tasks-list li {
    display: grid;
    grid-template-columns: 1.3rem 4.2rem auto minmax(0, 1fr);
    grid-template-areas: "glyph due toggle body";
    gap: 0.5rem;
    align-items: baseline;
    padding: 0.4rem 0;
    border-top: 1px solid rgba(28,31,42,0.08);
    font-size: 0.9rem;
    word-break: break-word;
  }
  .tasks-list li > :nth-child(1) { grid-area: glyph; }
  .tasks-list li > :nth-child(2) { grid-area: due; }
  .tasks-list li > :nth-child(3) { grid-area: toggle; }
  .tasks-list li > :nth-child(4) { grid-area: body; min-width: 0; }
  @media (max-width: 480px) {
    .tasks-list li {
      grid-template-columns: 1.2rem 1fr auto;
      grid-template-areas:
        "glyph due    toggle"
        "body  body   body";
      row-gap: 0.25rem;
    }
    .tasks-list li > :nth-child(3) { justify-self: end; }
    .tasks-list .due { font-size: 0.78rem; }
  }
  .tasks-list li:first-child { border-top: 0; }
  .tasks-list .tasks-more {
    display: block; grid-template-columns: none; grid-template-areas: none;
    padding-top: 0.5rem; border-top: 1px solid #9C9484;
    color: #5A5E6B; font-size: 0.78rem;
    letter-spacing: .04em;
  }
  .tasks-list .glyph { color: #5A5E6B; }
  .tasks-list .due { font-variant-numeric: tabular-nums; color: #5A5E6B; font-size: 0.82rem; }
  .tasks-list .title { font-weight: 500; }
  .tasks-list .context { color: #5A5E6B; font-size: 0.8rem; line-height: 1.35; }
  .tasks-list .toggle {
    background: none; border: 1px solid #9C9484; color: #5A5E6B;
    padding: 0 0.4rem; font-size: 0.75rem; cursor: pointer; border-radius: 2px;
    font-family: 'IBM Plex Mono', monospace;
  }
  .tasks-list .toggle:hover { background: #1B1F2A; color: #fff; border-color: #1B1F2A; }
  .tasks-list .row-overdue { background: rgba(180, 50, 50, 0.06); }
  .tasks-list .row-overdue .due { color: #b43232; font-weight: 600; }
  .tasks-list .row-red .due { color: #b43232; font-weight: 600; }
  .tasks-list .row-amber .due { color: #b8861f; }
  .tasks-list .row-done .title { text-decoration: line-through; color: #9C9484; }
  .tasks-counts { color: #5A5E6B; font-size: 0.78rem; margin-bottom: 0.6rem; }
</style>
"""


def _render_tasks(show_done: bool = False, show_all: bool = False) -> str:
    from datetime import date as _date
    today = _date.today()
    all_tasks = _tasks.load()
    open_ts = [t for t in all_tasks if not t.done]
    done_ts = [t for t in all_tasks if t.done]
    open_ts.sort(key=lambda t: (t.due is None, t.due or _date.max))
    done_ts.sort(key=lambda t: t.done_on or _date.min, reverse=True)

    # Default view: only the next 30 days of work (overdue/red/amber). Far-future
    # logistics live behind "show all" so the pane isn't dominated by 2027 tasks.
    if show_all:
        visible_open = open_ts
        hidden_count = 0
    else:
        visible_open = [t for t in open_ts if t.urgency(today) in {"overdue", "red", "amber"}]
        hidden_count = len(open_ts) - len(visible_open)

    overdue = sum(1 for t in open_ts if t.urgency(today) == "overdue")
    red = sum(1 for t in open_ts if t.urgency(today) == "red")
    counts = f"{len(open_ts)} open · {overdue} overdue · {red} due ≤7d · {len(done_ts)} done"

    def row_html(t: _tasks.Task) -> str:
        u = t.urgency(today)
        days = t.days_until(today)
        if t.done:
            due_label = f"done {t.done_on.isoformat()}" if t.done_on else "done"
        elif days is None:
            due_label = "—"
        elif days < 0:
            due_label = f"{-days}d late"
        elif days == 0:
            due_label = "today"
        elif days <= 30:
            due_label = f"in {days}d"
        else:
            due_label = t.due.isoformat() if t.due else "—"
        toggle_label = "undo" if t.done else "done"
        return (
            f'<li class="row-{u}">'
            f'<span class="glyph">{t.glyph}</span>'
            f'<span class="due">{due_label}</span>'
            f'<button class="toggle" hx-post="/api/tasks/{t.id}/toggle" '
            f'hx-target="#tasks" hx-swap="innerHTML">{toggle_label}</button>'
            f'<div><div class="title">{t.title}</div>'
            f'<div class="context">{t.context}</div></div>'
            f'</li>'
        )

    rows = "".join(row_html(t) for t in visible_open)
    if hidden_count and not show_all:
        rows += (
            f'<li class="tasks-more">'
            f'+ {hidden_count} more (due &gt; 30d)</li>'
        )
    if show_done and done_ts:
        rows += '<li style="border-top:1px solid #9C9484;padding-top:0.5rem;color:#5A5E6B;font-size:0.78rem;">— done —</li>'
        rows += "".join(row_html(t) for t in done_ts)

    link_style = 'cursor:pointer;color:#5A5E6B;font-size:0.78rem;'
    toggle_all = (
        f'<a hx-get="/api/tasks?show_all=0&show_done={int(show_done)}" '
        f'hx-target="#tasks" hx-swap="innerHTML" style="{link_style}">collapse future</a>'
        if show_all else
        f'<a hx-get="/api/tasks?show_all=1&show_done={int(show_done)}" '
        f'hx-target="#tasks" hx-swap="innerHTML" style="{link_style}">show all</a>'
    )
    toggle_done = (
        f'<a hx-get="/api/tasks?show_done=0&show_all={int(show_all)}" '
        f'hx-target="#tasks" hx-swap="innerHTML" style="{link_style}">hide done</a>'
        if show_done else
        f'<a hx-get="/api/tasks?show_done=1&show_all={int(show_all)}" '
        f'hx-target="#tasks" hx-swap="innerHTML" style="{link_style}">show done</a>'
    )

    return (
        f'{_TASK_ROW_CSS}'
        f'<div class="tasks-counts">{counts} · {toggle_all} · {toggle_done}</div>'
        f'<ul class="tasks-list">{rows}</ul>'
    )


@app.get("/api/tasks", response_class=HTMLResponse)
async def api_tasks(show_done: int = 0, show_all: int = 0):
    return _render_tasks(show_done=bool(show_done), show_all=bool(show_all))


@app.post("/api/tasks/{task_id}/toggle", response_class=HTMLResponse)
async def api_tasks_toggle(task_id: str):
    _tasks.toggle(task_id)
    return _render_tasks()


# ─────────── protein tracking ───────────

PROTEIN_MULT_LOW = 1.6   # g/kg, masters lower bound
PROTEIN_MULT_HIGH = 2.0  # g/kg, masters upper bound (in deficit)


def load_protein() -> "pd.DataFrame":
    rows = []
    d = DATA / "protein"
    if not d.exists():
        return pd.DataFrame()
    for p in sorted(d.glob("*.json")):
        try:
            obj = json.loads(p.read_text())
            rows.append({"date": p.stem, "grams": float(obj.get("grams") or 0)})
        except Exception:
            continue
    df = pd.DataFrame(rows)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)
    return df


def save_protein(d: date, grams: float) -> None:
    out = DATA / "protein"
    out.mkdir(parents=True, exist_ok=True)
    from datetime import datetime as _dt
    (out / f"{d.isoformat()}.json").write_text(json.dumps({
        "grams": round(max(0.0, grams), 1),
        "logged_at": _dt.now().isoformat(timespec="seconds"),
    }, indent=2))


def protein_target_floor() -> tuple[float, float, float]:
    """Returns (floor_g, high_g, current_kg). Uses current weight; falls back to goal weight."""
    wdf = load_weight()
    kg = float(wdf.iloc[-1]["kg"]) if not wdf.empty else WEIGHT_GOAL_KG
    return PROTEIN_MULT_LOW * kg, PROTEIN_MULT_HIGH * kg, kg


def protein_chart() -> str:
    df = load_protein().tail(30)
    floor_g, high_g, _kg = protein_target_floor()
    if df.empty:
        return "<div class='empty'>no protein entries yet</div>"
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=df["date"], y=df["grams"],
        marker_color=OXIDE, marker_line=dict(width=0),
        name="g/day",
    ))
    fig.add_hline(y=floor_g, line=dict(color=FOREST, width=1.2, dash="dash"),
                  annotation_text=f"1.6 g/kg = {floor_g:.0f} g",
                  annotation_position="top left",
                  annotation_font=dict(size=9, color=FOREST))
    fig.add_hline(y=high_g, line=dict(color=FOREST, width=0.8, dash="dot"),
                  annotation_text=f"2.0 g/kg = {high_g:.0f} g",
                  annotation_position="top right",
                  annotation_font=dict(size=9, color=FOREST))
    fig.update_layout(**chart_layout())
    fig.update_yaxes(title=dict(text="g/day", font=dict(size=10, color=INK_SOFT)))
    return chart_html(fig, "protein-chart")


PROTEIN_PARTIAL = Template("""
<div class="stat-row">
  <div class="stat-big">{{last_g}}</div>
  <div class="stat-unit">g · floor {{floor}} g · roof {{roof}} g · at {{kg}} kg</div>
  <a hx-get="/api/protein?edit=1" hx-target="#protein" hx-swap="innerHTML"
     style="cursor:pointer;color:#5A5E6B;font-size:0.78rem;margin-left:0.6rem;">edit</a>
</div>
{% if insight %}<div class="insight">{{insight}}</div>{% endif %}
{{chart|safe}}
""")

PROTEIN_FORM_PARTIAL = Template("""
<form class="alcohol-form" hx-post="/api/protein" hx-target="#protein" hx-swap="innerHTML">
  <label>date <input name="for_date" value="{{default_date}}" required></label>
  <label>grams <input name="grams" type="number" step="1" min="0" value="{{default_grams}}" required></label>
  <button type="submit">save</button>
  <a hx-get="/api/protein" hx-target="#protein" hx-swap="innerHTML"
     style="cursor:pointer;color:#5A5E6B;font-size:0.78rem;margin-left:0.6rem;">cancel</a>
</form>
{{chart|safe}}
""")


def _protein_for(df: "pd.DataFrame", d: date) -> float:
    if df.empty:
        return 0.0
    row = df[df["date"] == pd.Timestamp(d)]
    return float(row["grams"].iloc[-1]) if not row.empty else 0.0


def _protein_insight(df: "pd.DataFrame", floor_g: float) -> str:
    if df.empty:
        return ""
    last7 = df.tail(7)
    if last7.empty:
        return ""
    avg = last7["grams"].mean()
    short = (avg < floor_g)
    pct = (avg / floor_g * 100) if floor_g else 0
    flag = "⚠" if short else "✓"
    return f"{flag} 7-day avg {avg:.0f} g ({pct:.0f}% of floor)"


@app.get("/api/protein", response_class=HTMLResponse)
async def api_protein(edit: int = 0):
    df = load_protein()
    today = date.today()
    yesterday = today - timedelta(days=1)
    floor_g, high_g, kg = protein_target_floor()
    if edit:
        return PROTEIN_FORM_PARTIAL.render(
            default_date=eu_date(yesterday),
            default_grams=f"{_protein_for(df, yesterday):.0f}",
            chart=protein_chart(),
        )
    # Show most-recently logged day
    if not df.empty and (df["date"] == pd.Timestamp(today)).any():
        last_g = _protein_for(df, today)
    else:
        last_g = _protein_for(df, yesterday)
    return PROTEIN_PARTIAL.render(
        last_g=f"{last_g:.0f}",
        floor=f"{floor_g:.0f}",
        roof=f"{high_g:.0f}",
        kg=f"{kg:.1f}",
        insight=_protein_insight(df, floor_g),
        chart=protein_chart(),
    )


@app.post("/api/protein", response_class=HTMLResponse)
async def api_protein_post(grams: float = Form(...), for_date: str = Form(...)):
    save_protein(_parse_eu_or_iso(for_date), grams)
    return await api_protein()


# ─────────── HRmax calibration banner ───────────

@app.get("/api/calibration", response_class=HTMLResponse)
async def api_calibration():
    """Tiny status pill showing whether HRmax has been measured.
    Reads task `hrmax-test` done state from tasks.yaml."""
    ts = {t.id: t for t in _tasks.load()}
    hrmax_done = ts.get("hrmax-test") and ts["hrmax-test"].done
    knee_mri_done = ts.get("knee-mri") and ts["knee-mri"].done
    pieces = []
    if not hrmax_done:
        pieces.append('<span style="color:#b43232;">⚠ HRmax not measured — zone targets are estimates</span>')
    else:
        pieces.append('<span style="color:#3f7d3f;">✓ HRmax measured</span>')
    if not knee_mri_done:
        pieces.append('<span style="color:#b8861f;">⚠ knee MRI not in references/</span>')
    return (
        '<div style="font-size:0.78rem;color:#5A5E6B;margin:0.2rem 0 0.6rem 0;'
        'display:flex;gap:1rem;flex-wrap:wrap;">'
        + " · ".join(pieces) +
        '</div>'
    )


# ─────────── knee score (0–10) ───────────

def load_knee() -> "pd.DataFrame":
    rows = []
    d = DATA / "knee"
    if not d.exists():
        return pd.DataFrame()
    for p in sorted(d.glob("*.json")):
        try:
            obj = json.loads(p.read_text())
            rows.append({"date": p.stem, "score": float(obj.get("score") or 0)})
        except Exception:
            continue
    df = pd.DataFrame(rows)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)
    return df


def save_knee(d: date, score: float) -> None:
    out = DATA / "knee"
    out.mkdir(parents=True, exist_ok=True)
    from datetime import datetime as _dt
    (out / f"{d.isoformat()}.json").write_text(json.dumps({
        "score": round(max(0.0, min(10.0, score)), 1),
        "logged_at": _dt.now().isoformat(timespec="seconds"),
    }, indent=2))


# ─────────── unified daily log ───────────

# Unicode sparkline blocks. Index 0 = visible-but-minimal, 8 = full.
_SPARK_BLOCKS = "▁▂▃▄▅▆▇█"
_SPARK_EMPTY = "·"  # for missing days


def _spark(values: list[float | None], scale_max: float, width: int = 7) -> str:
    """Render a 7-char unicode sparkline. None = missing day (rendered as dot)."""
    out = []
    for v in values[-width:]:
        if v is None:
            out.append(_SPARK_EMPTY)
        elif v <= 0:
            out.append(_SPARK_BLOCKS[0])
        else:
            idx = min(len(_SPARK_BLOCKS) - 1, int(v / scale_max * (len(_SPARK_BLOCKS) - 1)))
            out.append(_SPARK_BLOCKS[max(0, idx)])
    # left-pad if shorter than width
    while len(out) < width:
        out.insert(0, _SPARK_EMPTY)
    return "".join(out)


def _last7_values(df: "pd.DataFrame", col: str, today: date) -> list[float | None]:
    """Return 7 values for [today-6 .. today], None where the day is missing."""
    if df.empty:
        return [None] * 7
    idx = df.set_index("date")[col]
    out: list[float | None] = []
    for i in range(6, -1, -1):
        d = today - timedelta(days=i)
        ts = pd.Timestamp(d)
        out.append(float(idx[ts]) if ts in idx.index else None)
    return out


LOG_PANEL = Template("""
<style>
  .log-panel { font-family: 'IBM Plex Mono', monospace; color: var(--ink); }
  .log-stamp {
    display: inline-block; padding: 0.15rem 0.5rem; font-size: 0.78rem;
    border: 1px solid var(--ink); letter-spacing: 0.08em; text-transform: uppercase;
    transform: rotate(-1.5deg); color: var(--ink); background: var(--paper);
    margin-bottom: 0.8rem;
  }
  .log-form { display: grid; gap: 0.6rem; margin-bottom: 1rem; }
  .log-row {
    display: grid;
    grid-template-columns: 1.6rem 5.5rem auto minmax(0, 1fr);
    grid-template-areas: "ord lbl field hint";
    gap: 0.6rem; align-items: baseline;
    padding-bottom: 0.45rem;
    border-bottom: 1px dashed rgba(28,31,42,0.18);
  }
  .log-row > :nth-child(1) { grid-area: ord; }
  .log-row > :nth-child(2) { grid-area: lbl; }
  .log-row > :nth-child(3) { grid-area: field; }
  .log-row > :nth-child(4) { grid-area: hint; min-width: 0; word-break: break-word; }
  @media (max-width: 480px) {
    .log-row {
      grid-template-columns: 1.4rem 1fr auto;
      grid-template-areas:
        "ord  lbl  field"
        "hint hint hint";
      row-gap: 0.15rem;
    }
    .log-row > :nth-child(4) { font-size: 0.72rem; padding-top: 0.1rem; }
  }
  .log-row .ord { color: #9C9484; font-size: 0.85rem; }
  .log-row .lbl { font-size: 0.95rem; letter-spacing: 0.02em; }
  .log-row .field {
    font-family: inherit; font-size: 1.5rem; font-weight: 500;
    color: var(--ink); background: transparent; border: 0;
    border-bottom: 1.5px solid var(--ink); width: 5.5rem;
    text-align: right; padding: 0 0.2rem 0.05rem;
    font-variant-numeric: tabular-nums;
  }
  .log-row .field:focus { outline: 0; border-bottom-color: var(--oxide); }
  .log-row .hint { font-size: 0.78rem; color: var(--ink-soft); }

  .log-action { display: flex; justify-content: space-between; align-items: center;
                margin-top: 0.5rem; }
  .log-action .date-field {
    font-family: inherit; font-size: 0.85rem; color: var(--ink-soft);
    background: transparent; border: 0;
    border-bottom: 1px dotted var(--ink-soft); padding: 0.1rem 0.2rem;
  }
  .log-action button.stamp {
    font-family: inherit; font-size: 0.85rem; letter-spacing: 0.15em;
    text-transform: uppercase; padding: 0.4rem 1rem;
    background: var(--ink); color: var(--paper); border: 0;
    cursor: pointer;
  }
  .log-action button.stamp:hover { background: var(--oxide); }

  .log-week { margin-top: 0.9rem; padding-top: 0.6rem;
              border-top: 1px solid rgba(28,31,42,0.15); }
  .log-week .head { font-size: 0.72rem; letter-spacing: 0.18em;
                    text-transform: uppercase; color: #9C9484;
                    margin-bottom: 0.4rem; }
  .log-week .row { display: grid;
                   grid-template-columns: 7rem auto 1fr;
                   gap: 0.8rem; align-items: baseline;
                   font-size: 0.85rem; margin-bottom: 0.15rem; }
  .log-week .spark {
    font-family: 'IBM Plex Mono', monospace; font-size: 1.1rem;
    letter-spacing: 0.18em; color: var(--ink);
  }
  .log-week .summary { color: var(--ink-soft); font-size: 0.8rem; }
  .log-saved { color: var(--forest); font-size: 0.78rem; margin-left: 0.5rem; }
</style>

<div class="log-panel">
  <span class="log-stamp">entry · {{ stamp_date }}</span>
  {% if saved %}<span class="log-saved">stamped ✓</span>{% endif %}

  <form class="log-form" hx-post="/api/log" hx-target="#log" hx-swap="innerHTML">
    <div class="log-row">
      <span class="ord">i.</span>
      <span class="lbl">protein</span>
      <input class="field" type="number" step="1" min="0" name="protein"
             value="{{ protein_val }}" />
      <span class="hint">grams · floor {{ floor_g }}g · roof {{ roof_g }}g</span>
    </div>
    <div class="log-row">
      <span class="ord">ii.</span>
      <span class="lbl">alcohol</span>
      <input class="field" type="number" step="0.5" min="0" name="alcohol"
             value="{{ alcohol_val }}" />
      <span class="hint">standard units · {{ alc_insight }}</span>
    </div>
    <div class="log-row">
      <span class="ord">iii.</span>
      <span class="lbl">knee</span>
      <input class="field" type="number" step="1" min="0" max="10" name="knee"
             value="{{ knee_val }}" />
      <span class="hint">0–10 · 0=fine · 3=watch · 5+=stop</span>
    </div>

    <div class="log-action">
      <label style="color: var(--ink-soft); font-size: 0.78rem;">
        for date
        <input class="date-field" name="for_date" value="{{ for_date }}" />
      </label>
      <button class="stamp" type="submit">stamp it</button>
    </div>
  </form>

  <div class="log-week">
    <div class="head">this week · seven days back</div>
    <div class="row">
      <span class="lbl">protein</span>
      <span class="spark">{{ spark_p }}</span>
      <span class="summary">{{ summary_p }}</span>
    </div>
    <div class="row">
      <span class="lbl">alcohol</span>
      <span class="spark">{{ spark_a }}</span>
      <span class="summary">{{ summary_a }}</span>
    </div>
    <div class="row">
      <span class="lbl">knee</span>
      <span class="spark">{{ spark_k }}</span>
      <span class="summary">{{ summary_k }}</span>
    </div>
  </div>
</div>
""")


def _render_log(saved: bool = False) -> str:
    today = date.today()
    yesterday = today - timedelta(days=1)
    floor_g, roof_g, _kg = protein_target_floor()

    pdf = load_protein()
    adf = load_alcohol()
    kdf = load_knee()

    # Default form values: prefer today's logged, else yesterday's logged, else blank
    def latest_or_blank(df, col: str) -> str:
        if df.empty:
            return ""
        for d in (today, yesterday):
            ts = pd.Timestamp(d)
            row = df[df["date"] == ts]
            if not row.empty:
                v = row[col].iloc[-1]
                return f"{v:g}"
        return ""

    # 7-day spark + summary
    p7 = _last7_values(pdf, "grams", today)
    a7 = _last7_values(adf, "units", today)
    k7 = _last7_values(kdf, "score", today)

    p_vals = [v for v in p7 if v is not None]
    a_vals = [v for v in a7 if v is not None]
    k_vals = [v for v in k7 if v is not None]

    p_mean = (sum(p_vals) / len(p_vals)) if p_vals else 0.0
    a_total = sum(a_vals) if a_vals else 0.0
    a_days = sum(1 for v in a_vals if v > 0)
    k_max = max(k_vals) if k_vals else 0.0

    p_summary = f"avg {p_mean:.0f} g/day · {len(p_vals)} of 7 logged" if p_vals else "no entries"
    a_summary = f"{a_total:g} u total · {a_days} drinking day(s)" if a_vals else "no entries"
    if k_vals:
        k_label = "fine" if k_max <= 2 else ("watch" if k_max <= 4 else "stop")
        k_summary = f"peak {k_max:g}/10 · {k_label}"
    else:
        k_summary = "no entries"

    return LOG_PANEL.render(
        stamp_date=today.strftime("%a · %d %b %Y").upper(),
        saved=saved,
        protein_val=latest_or_blank(pdf, "grams"),
        alcohol_val=latest_or_blank(adf, "units"),
        knee_val=latest_or_blank(kdf, "score"),
        floor_g=f"{floor_g:.0f}",
        roof_g=f"{roof_g:.0f}",
        alc_insight=alcohol_hrv_insight() or "no HRV comparison yet",
        for_date=eu_date(today),
        spark_p=_spark(p7, scale_max=max(roof_g, 1.0)),
        spark_a=_spark(a7, scale_max=5.0),
        spark_k=_spark(k7, scale_max=10.0),
        summary_p=p_summary,
        summary_a=a_summary,
        summary_k=k_summary,
    )


@app.get("/api/log", response_class=HTMLResponse)
async def api_log():
    return _render_log()


@app.post("/api/log", response_class=HTMLResponse)
async def api_log_post(
    protein: str = Form(""),
    alcohol: str = Form(""),
    knee: str = Form(""),
    for_date: str = Form(...),
):
    d = _parse_eu_or_iso(for_date)
    if protein.strip():
        save_protein(d, float(protein))
    if alcohol.strip():
        save_alcohol(d, float(alcohol))
    if knee.strip():
        save_knee(d, float(knee))
    return _render_log(saved=True)


