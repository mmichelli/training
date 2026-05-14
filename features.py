"""Compute training features from ingested Garmin data.

Inputs (data/ — populated by ingest):
  activities/*.md   — per-activity markdown (existing)
  sleep/*.json      — daily sleep
  hrv/*.json        — overnight HRV
  stress/*.json     — daily stress
  weight/*.json     — weigh-ins
  daily/*.json      — daily summary (steps, RHR, body battery)

Outputs:
  features.parquet  — one row per day with the metrics that actually matter:

  load_acute_7d        — exponentially-weighted training load, 7d τ
  load_chronic_42d     — exponentially-weighted training load, 42d τ
  acwr                 — load_acute_7d / load_chronic_42d  (cap at 1.3)
  hrv_baseline_60d     — rolling mean of overnight rMSSD
  hrv_7d               — rolling 7d mean
  hrv_z                — (hrv_7d - hrv_baseline_60d) / sd_60d
  sleep_h_7d           — rolling 7d mean sleep hours
  sleep_debt           — sum(target_h - sleep_h) over 7 days, clamped >=0
  rhr_baseline_60d     — rolling resting HR baseline
  rhr_delta            — rhr_today - rhr_baseline_60d
  cadence_baseline     — rolling 30d mean cadence on easy runs
  hr_drift_last_run    — slope of HR/min at iso-pace, last steady run

Readiness verdict (3-light):
  red    — HRV_z < -1.0, OR rhr_delta > +5 bpm, OR ACWR > 1.5
  amber  — HRV_z < -0.5 OR sleep_debt > 4h OR ACWR > 1.3
  green  — otherwise

Sources for the choice of metrics: research note in conversation.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent
DATA = ROOT / "data"


@dataclass
class Verdict:
    light: str  # "green" | "amber" | "red"
    reasons: list[str]


def load_daily(stream: str) -> pd.DataFrame:
    """Load all data/<stream>/*.json into a date-indexed dataframe."""
    d = DATA / stream
    if not d.exists():
        return pd.DataFrame()
    rows = []
    for p in d.glob("*.json"):
        try:
            rec = json.loads(p.read_text())
        except Exception:
            continue
        rec.setdefault("date", p.stem)
        rows.append(rec)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df.sort_values("date").reset_index(drop=True)


def load_activities() -> pd.DataFrame:
    """Read the markdown activity files' frontmatter."""
    d = ROOT / "activities"
    if not d.exists():
        return pd.DataFrame()
    rows = []
    for p in sorted(d.glob("*.md")):
        text = p.read_text()
        if not text.startswith("---"):
            continue
        end = text.find("\n---", 3)
        fm = {}
        for line in text[3:end].splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                fm[k.strip()] = v.strip().strip('"')
        if not fm.get("date"):
            continue
        rows.append(fm)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    for col in ("distance_km", "duration_s", "moving_s", "avg_hr", "max_hr", "elev_gain_m"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def training_load(activities: pd.DataFrame) -> pd.Series:
    """Daily TRIMP-like load. Falls back to moving_s if HR missing."""
    if activities.empty:
        return pd.Series(dtype=float)
    a = activities.copy()
    a["load"] = (a["moving_s"].fillna(0) / 60).clip(lower=0)
    # weight by HR intensity when available: load * (avg_hr / 140)^2
    has_hr = a["avg_hr"].notna() & (a["avg_hr"] > 0)
    a.loc[has_hr, "load"] *= (a.loc[has_hr, "avg_hr"] / 140) ** 2
    return a.groupby("date")["load"].sum()


def ewma_load(daily_load: pd.Series, tau_days: int) -> pd.Series:
    if daily_load.empty:
        return daily_load
    full_idx = pd.date_range(daily_load.index.min(), daily_load.index.max(), freq="D").date
    s = daily_load.reindex(full_idx, fill_value=0)
    return s.ewm(halflife=tau_days, adjust=False).mean()


def compute_features() -> pd.DataFrame:
    acts = load_activities()
    sleep = load_daily("sleep")
    hrv = load_daily("hrv")
    daily = load_daily("daily")

    if acts.empty and sleep.empty and hrv.empty and daily.empty:
        return pd.DataFrame()

    load = training_load(acts)
    load_acute = ewma_load(load, 7)
    load_chronic = ewma_load(load, 42)

    df = pd.DataFrame(index=load.index if not load.empty else pd.Index([], name="date"))
    if not load.empty:
        df["load"] = load
        df["load_acute_7d"] = load_acute
        df["load_chronic_42d"] = load_chronic
        df["acwr"] = (load_acute / load_chronic.replace(0, pd.NA)).astype(float)

    if not hrv.empty:
        h = hrv.set_index("date")["rmssd"] if "rmssd" in hrv.columns else None
        if h is not None:
            df = df.join(h.rename("hrv_rmssd"), how="outer")
            df["hrv_7d"] = df["hrv_rmssd"].rolling(7, min_periods=3).mean()
            df["hrv_baseline_60d"] = df["hrv_rmssd"].rolling(60, min_periods=14).mean()
            df["hrv_sd_60d"] = df["hrv_rmssd"].rolling(60, min_periods=14).std()
            df["hrv_z"] = (df["hrv_7d"] - df["hrv_baseline_60d"]) / df["hrv_sd_60d"]

    if not sleep.empty and "sleep_hours" in sleep.columns:
        s = sleep.set_index("date")["sleep_hours"]
        df = df.join(s.rename("sleep_h"), how="outer")
        df["sleep_h_7d"] = df["sleep_h"].rolling(7, min_periods=3).mean()
        # debt vs 7.5h target
        df["sleep_debt"] = (7.5 - df["sleep_h"]).clip(lower=0).rolling(7, min_periods=3).sum()

    if not daily.empty and "resting_hr" in daily.columns:
        r = daily.set_index("date")["resting_hr"]
        df = df.join(r.rename("rhr"), how="outer")
        df["rhr_baseline_60d"] = df["rhr"].rolling(60, min_periods=14).mean()
        df["rhr_delta"] = df["rhr"] - df["rhr_baseline_60d"]

    df = df.sort_index()
    return df


def readiness(latest: pd.Series) -> Verdict:
    reasons: list[str] = []
    light = "green"

    def amber(reason: str):
        nonlocal light
        reasons.append(reason)
        if light == "green":
            light = "amber"

    def red(reason: str):
        nonlocal light
        reasons.append(reason)
        light = "red"

    if "hrv_z" in latest and pd.notna(latest["hrv_z"]):
        if latest["hrv_z"] < -1.0:
            red(f"HRV z={latest['hrv_z']:+.2f} (suppressed)")
        elif latest["hrv_z"] < -0.5:
            amber(f"HRV z={latest['hrv_z']:+.2f} (mildly down)")

    if "rhr_delta" in latest and pd.notna(latest["rhr_delta"]):
        if latest["rhr_delta"] > 5:
            red(f"RHR +{latest['rhr_delta']:.0f} above baseline")
        elif latest["rhr_delta"] > 3:
            amber(f"RHR +{latest['rhr_delta']:.0f} vs baseline")

    if "acwr" in latest and pd.notna(latest["acwr"]):
        if latest["acwr"] > 1.5:
            red(f"ACWR {latest['acwr']:.2f} (overreach risk)")
        elif latest["acwr"] > 1.3:
            amber(f"ACWR {latest['acwr']:.2f} (high)")

    if "sleep_debt" in latest and pd.notna(latest["sleep_debt"]):
        if latest["sleep_debt"] > 6:
            red(f"sleep debt {latest['sleep_debt']:.1f}h over 7d")
        elif latest["sleep_debt"] > 4:
            amber(f"sleep debt {latest['sleep_debt']:.1f}h over 7d")

    if not reasons:
        reasons.append("nothing flagged")
    return Verdict(light=light, reasons=reasons)


if __name__ == "__main__":
    df = compute_features()
    out = ROOT / "features.parquet"
    if df.empty:
        print("no data yet — ingest first")
    else:
        df.to_parquet(out)
        print(f"wrote {out}: {len(df)} days, columns={list(df.columns)}")
        if not df.empty:
            v = readiness(df.iloc[-1])
            print(f"\nReadiness today: {v.light.upper()}")
            for r in v.reasons:
                print(f"  · {r}")
