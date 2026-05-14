"""Visual training dashboard. Run: uv run streamlit run dashboard_app.py."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

import features as F
from plan_lookup import PLAN_START, WEEKLY_TOTAL, prescription_for

ROOT = Path(__file__).parent

st.set_page_config(page_title="Two Oceans 2027", layout="wide")


@st.cache_data(ttl=300)
def get_features() -> pd.DataFrame:
    return F.compute_features()


df = get_features()
today = date.today()
plan_week = ((today - PLAN_START).days // 7) + 1

# ── Header
st.title("Two Oceans 2027 — training dashboard")
sub = f"**{today:%a %d %b %Y}** · plan week **{plan_week}** · target **{WEEKLY_TOTAL.get(plan_week, 0):.1f}h**"
st.markdown(sub)

# ── Readiness traffic light
col1, col2 = st.columns([1, 3])
with col1:
    if df.empty:
        st.warning("No data yet — run ingest")
        v = None
    else:
        v = F.readiness(df.iloc[-1])
        colors = {"green": "🟢", "amber": "🟡", "red": "🔴"}
        labels = {"green": "Train normally", "amber": "Easy only", "red": "Rest / recover"}
        st.markdown(f"## {colors[v.light]} {labels[v.light]}")
with col2:
    if v is not None:
        st.markdown("**Why:**")
        for r in v.reasons:
            st.markdown(f"- {r}")

st.divider()

# ── Today's prescription
p = prescription_for(today)
st.subheader(f"Today — {p.weekday}: {p.title}")
st.caption(f"{p.phase} · purpose: {p.purpose}")
st.markdown(p.description)

st.divider()

# ── Charts
if not df.empty:
    tabs = st.tabs(["Load & ACWR", "HRV", "Sleep & RHR", "Weekly volume"])

    with tabs[0]:
        if "load" in df.columns:
            d = df[["load_acute_7d", "load_chronic_42d"]].dropna(how="all").tail(120).reset_index(names="date")
            fig = px.line(d, x="date", y=["load_acute_7d", "load_chronic_42d"], title="Training load (EWMA)")
            st.plotly_chart(fig, use_container_width=True)
            if "acwr" in df.columns:
                d2 = df[["acwr"]].dropna().tail(120).reset_index(names="date")
                fig2 = px.line(d2, x="date", y="acwr", title="ACWR (cap recommended at 1.3)")
                fig2.add_hline(y=1.3, line_dash="dash", line_color="orange")
                fig2.add_hline(y=1.5, line_dash="dash", line_color="red")
                st.plotly_chart(fig2, use_container_width=True)
        else:
            st.info("No activity load yet.")

    with tabs[1]:
        if "hrv_rmssd" in df.columns:
            d = df[["hrv_rmssd", "hrv_7d", "hrv_baseline_60d"]].dropna(how="all").tail(120).reset_index(names="date")
            fig = px.line(d, x="date", y=["hrv_rmssd", "hrv_7d", "hrv_baseline_60d"],
                          title="Overnight HRV (rMSSD)")
            st.plotly_chart(fig, use_container_width=True)
            if "hrv_z" in df.columns:
                d2 = df[["hrv_z"]].dropna().tail(120).reset_index(names="date")
                fig2 = px.line(d2, x="date", y="hrv_z", title="HRV z-score (7d vs 60d)")
                fig2.add_hline(y=-0.5, line_dash="dash", line_color="orange")
                fig2.add_hline(y=-1.0, line_dash="dash", line_color="red")
                st.plotly_chart(fig2, use_container_width=True)
        else:
            st.info("No HRV data yet.")

    with tabs[2]:
        cols = [c for c in ["sleep_h", "sleep_h_7d", "rhr", "rhr_baseline_60d"] if c in df.columns]
        if cols:
            d = df[cols].dropna(how="all").tail(120).reset_index(names="date")
            if "sleep_h" in cols:
                st.plotly_chart(px.line(d, x="date", y=[c for c in cols if c.startswith("sleep")], title="Sleep"),
                                use_container_width=True)
            if "rhr" in cols:
                st.plotly_chart(px.line(d, x="date", y=[c for c in cols if c.startswith("rhr")], title="Resting HR"),
                                use_container_width=True)
        else:
            st.info("No sleep/RHR data yet.")

    with tabs[3]:
        acts = F.load_activities()
        if not acts.empty:
            acts["week"] = pd.to_datetime(acts["date"]).dt.to_period("W-MON").apply(lambda r: r.start_time.date())
            wk = acts.groupby("week")["duration_s"].sum().div(3600).rename("actual_h").to_frame()
            wk["plan_week"] = [((d - PLAN_START).days // 7) + 1 for d in wk.index]
            wk["target_h"] = wk["plan_week"].map(WEEKLY_TOTAL).fillna(0)
            wk = wk.reset_index()
            fig = px.bar(wk.tail(12), x="week", y=["actual_h", "target_h"], barmode="group",
                         title="Weekly volume — actual vs target")
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No activities yet.")
else:
    st.info("Run ingest to populate the dashboard.")
