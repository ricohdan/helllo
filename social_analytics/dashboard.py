"""
Streamlit dashboard for Social Media Analytics.

Run with:
    streamlit run dashboard.py

Requires a filled-in .env file and at least one `python main.py collect` run.
"""

from __future__ import annotations

import os
import sys
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import streamlit as st
import plotly.graph_objects as go
import plotly.express as px
import pandas as pd
from dotenv import load_dotenv

# Load .env from the same directory as this file
load_dotenv(Path(__file__).parent / ".env")

# Make sure sibling modules are importable
sys.path.insert(0, str(Path(__file__).parent))
import db
import analytics as anlyt
from reporter import PlatformReport, send_email_report, send_slack_report

# ------------------------------------------------------------------
# Page config
# ------------------------------------------------------------------
st.set_page_config(
    page_title="Social Media Analytics",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ------------------------------------------------------------------
# Minimal style tweaks
# ------------------------------------------------------------------
st.markdown(
    """
    <style>
    .metric-card {
        background: #1e1e2e;
        border-radius: 10px;
        padding: 18px 24px;
        color: #fff;
    }
    .metric-card .label { font-size: 13px; color: #aaa; margin-bottom: 4px; }
    .metric-card .value { font-size: 28px; font-weight: 700; }
    .metric-card .delta { font-size: 13px; margin-top: 4px; }
    .delta-pos { color: #4ade80; }
    .delta-neg { color: #f87171; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _fmt_delta(value, pct=None):
    if value is None:
        return ""
    sign = "+" if value >= 0 else ""
    cls = "delta-pos" if value >= 0 else "delta-neg"
    text = f"{sign}{value:,}"
    if pct is not None:
        text += f" ({sign}{pct}%)"
    return f'<span class="{cls}">{text}</span>'


def metric_card(label: str, value: str, delta_html: str = ""):
    st.markdown(
        f"""
        <div class="metric-card">
          <div class="label">{label}</div>
          <div class="value">{value}</div>
          {"<div class='delta'>" + delta_html + "</div>" if delta_html else ""}
        </div>
        """,
        unsafe_allow_html=True,
    )


def available_platforms() -> list[str]:
    platforms = []
    if os.environ.get("INSTAGRAM_ACCESS_TOKEN"):
        platforms.append("instagram")
    if os.environ.get("TIKTOK_ACCESS_TOKEN"):
        platforms.append("tiktok")
    return platforms or ["instagram", "tiktok"]  # show both even if no creds yet


# ------------------------------------------------------------------
# Data loaders (cached so re-runs are fast)
# ------------------------------------------------------------------

@st.cache_data(ttl=300)
def load_posts(platform: str, days_back: int) -> list[dict]:
    return db.get_posts(platform, days_back=days_back)


@st.cache_data(ttl=300)
def load_snapshots(platform: str, limit: int = 90) -> list[dict]:
    return db.get_snapshots(platform, limit=limit)


# ------------------------------------------------------------------
# Sidebar
# ------------------------------------------------------------------

with st.sidebar:
    st.title("📊 Social Analytics")
    st.divider()

    platforms = available_platforms()
    platform_label = st.selectbox(
        "Platform",
        options=platforms,
        format_func=lambda p: p.upper(),
    )

    days_back = st.slider("Look-back window (days)", min_value=7, max_value=90, value=30, step=7)

    st.divider()
    st.subheader("Actions")

    collect_btn = st.button("⬇️  Collect now", use_container_width=True)
    report_btn  = st.button("📬  Send report now", use_container_width=True, type="primary")

    if collect_btn:
        with st.spinner("Collecting data from APIs…"):
            result = subprocess.run(
                [sys.executable, str(Path(__file__).parent / "main.py"), "collect"],
                capture_output=True, text=True,
            )
        if result.returncode == 0:
            st.success("Data collected!")
            st.cache_data.clear()
        else:
            st.error(f"Collection failed:\n{result.stderr[-500:]}")

    if report_btn:
        with st.spinner("Generating and sending report…"):
            result = subprocess.run(
                [sys.executable, str(Path(__file__).parent / "main.py"), "report"],
                capture_output=True, text=True,
            )
        if result.returncode == 0:
            st.success("Report sent!")
        else:
            st.error(f"Report failed:\n{result.stderr[-500:]}")

    st.divider()
    st.caption("Data refreshes every 5 min. Use 'Collect now' for live data.")

# ------------------------------------------------------------------
# Load data
# ------------------------------------------------------------------

db.init_db()
posts      = load_posts(platform_label, days_back)
snapshots  = load_snapshots(platform_label)

if not snapshots and not posts:
    st.warning(
        f"No data yet for **{platform_label.upper()}**. "
        "Click **⬇️ Collect now** in the sidebar to pull your first batch of data."
    )
    st.stop()

# Build analytics objects
latest_snap   = max(snapshots, key=lambda s: s["captured_at"]) if snapshots else {}
followers     = latest_snap.get("followers") or 1
growth        = anlyt.follower_growth(snapshots)
top           = anlyt.top_posts(posts, followers, n=5)
bottom        = anlyt.bottom_posts(posts, followers, n=3)
type_breakdown = anlyt.content_type_breakdown(posts)
timing        = anlyt.best_posting_times(posts)

now           = datetime.now(timezone.utc)
one_week_ago  = now - timedelta(days=7)
two_weeks_ago = now - timedelta(days=14)

this_week_posts = [
    p for p in posts
    if p.get("posted_at") and p["posted_at"] >= one_week_ago.isoformat()
]
last_week_posts = [
    p for p in posts
    if p.get("posted_at")
    and two_weeks_ago.isoformat() <= p["posted_at"] < one_week_ago.isoformat()
]
wow  = anlyt.week_over_week_summary(this_week_posts, last_week_posts, followers)
recs = anlyt.generate_recommendations(timing, type_breakdown, growth, wow)

# ------------------------------------------------------------------
# Page header
# ------------------------------------------------------------------

st.title(f"{platform_label.upper()} — Analytics Dashboard")
st.caption(f"Last {days_back} days · as of {datetime.now(timezone.utc).strftime('%b %d, %Y %H:%M UTC')}")

# ------------------------------------------------------------------
# KPI row
# ------------------------------------------------------------------

k1, k2, k3, k4 = st.columns(4)

with k1:
    delta_html = _fmt_delta(growth.get("delta"), growth.get("delta_pct"))
    metric_card("Followers", f"{followers:,}", delta_html)

with k2:
    avg_er = wow.get("this_week", {}).get("avg_engagement_rate", 0)
    last_er = wow.get("last_week", {}).get("avg_engagement_rate", 0)
    er_delta = round(avg_er - last_er, 2) if last_er else None
    metric_card("Avg Engagement Rate", f"{avg_er:.2f}%", _fmt_delta(er_delta))

with k3:
    total_reach = wow.get("this_week", {}).get("total_reach", 0)
    last_reach  = wow.get("last_week", {}).get("total_reach", 0)
    reach_delta = total_reach - last_reach if last_reach else None
    metric_card("Reach (this week)", f"{total_reach:,}", _fmt_delta(reach_delta))

with k4:
    posts_this_wk = wow.get("this_week", {}).get("posts", 0)
    metric_card("Posts this week", str(posts_this_wk))

st.markdown("<br>", unsafe_allow_html=True)

# ------------------------------------------------------------------
# Row 2: Follower growth chart
# ------------------------------------------------------------------

st.subheader("Follower Growth")

if len(snapshots) >= 2:
    snap_df = pd.DataFrame(snapshots).sort_values("captured_at")
    snap_df["captured_at"] = pd.to_datetime(snap_df["captured_at"])
    snap_df = snap_df.dropna(subset=["followers"])

    fig_growth = go.Figure()
    fig_growth.add_trace(go.Scatter(
        x=snap_df["captured_at"],
        y=snap_df["followers"],
        mode="lines+markers",
        line=dict(color="#6366f1", width=2),
        marker=dict(size=5),
        fill="tozeroy",
        fillcolor="rgba(99,102,241,0.1)",
        name="Followers",
    ))
    fig_growth.update_layout(
        height=280,
        margin=dict(l=0, r=0, t=10, b=0),
        xaxis_title=None,
        yaxis_title="Followers",
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(showgrid=False),
        yaxis=dict(gridcolor="rgba(150,150,150,0.1)"),
    )
    st.plotly_chart(fig_growth, use_container_width=True)
else:
    st.info("Need at least 2 data snapshots to draw the growth trend. Collect more data over a few days.")

st.divider()

# ------------------------------------------------------------------
# Row 3: Content-type breakdown  +  Week-over-week
# ------------------------------------------------------------------

col_type, col_wow = st.columns([3, 2])

with col_type:
    st.subheader("Content Type Performance")
    if type_breakdown:
        type_df = pd.DataFrame([
            {
                "Type": k,
                "Avg Likes": v["avg_likes"],
                "Avg Comments": v["avg_comments"],
                "Avg Shares": v["avg_shares"],
                "Count": v["count"],
            }
            for k, v in type_breakdown.items()
        ]).sort_values("Avg Likes", ascending=False)

        fig_type = px.bar(
            type_df,
            x="Type",
            y=["Avg Likes", "Avg Comments", "Avg Shares"],
            barmode="group",
            color_discrete_sequence=["#6366f1", "#f59e0b", "#10b981"],
            labels={"value": "Average", "variable": "Metric"},
        )
        fig_type.update_layout(
            height=300,
            margin=dict(l=0, r=0, t=10, b=0),
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            legend=dict(orientation="h", y=-0.2),
            xaxis=dict(showgrid=False),
            yaxis=dict(gridcolor="rgba(150,150,150,0.1)"),
        )
        st.plotly_chart(fig_type, use_container_width=True)
    else:
        st.info("No content-type data yet.")

with col_wow:
    st.subheader("This Week vs Last Week")
    this_w = wow.get("this_week", {})
    last_w = wow.get("last_week", {})

    wow_rows = [
        ("Posts", this_w.get("posts", 0), last_w.get("posts", 0)),
        ("Avg ER (%)", round(this_w.get("avg_engagement_rate", 0), 2), round(last_w.get("avg_engagement_rate", 0), 2)),
        ("Total Likes", this_w.get("total_likes", 0), last_w.get("total_likes", 0)),
        ("Total Comments", this_w.get("total_comments", 0), last_w.get("total_comments", 0)),
        ("Total Shares", this_w.get("total_shares", 0), last_w.get("total_shares", 0)),
        ("Total Reach", this_w.get("total_reach", 0), last_w.get("total_reach", 0)),
    ]

    wow_df = pd.DataFrame(wow_rows, columns=["Metric", "This week", "Last week"])
    wow_df["Change"] = wow_df.apply(
        lambda r: f"+{r['This week'] - r['Last week']}" if r["This week"] >= r["Last week"]
        else str(r["This week"] - r["Last week"]),
        axis=1,
    )
    st.dataframe(wow_df, use_container_width=True, hide_index=True, height=260)

st.divider()

# ------------------------------------------------------------------
# Row 4: Best posting times heatmap
# ------------------------------------------------------------------

st.subheader("Best Posting Times (Engagement Heatmap)")

heatmap_data = timing.get("heatmap", {})
if heatmap_data:
    day_order = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    all_hours = sorted({h for day in heatmap_data.values() for h in day.keys()})

    z_matrix = []
    for day in day_order:
        row = [heatmap_data.get(day, {}).get(h, 0) for h in all_hours]
        z_matrix.append(row)

    fig_heat = go.Figure(go.Heatmap(
        z=z_matrix,
        x=[f"{h:02d}:00" for h in all_hours],
        y=day_order,
        colorscale="Purples",
        showscale=True,
        hovertemplate="Day: %{y}<br>Hour: %{x}<br>Avg score: %{z:.1f}<extra></extra>",
    ))
    fig_heat.update_layout(
        height=280,
        margin=dict(l=0, r=0, t=10, b=0),
        xaxis_title="Hour (UTC)",
        yaxis_title=None,
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig_heat, use_container_width=True)

    best_days_str  = ", ".join(f"{d[0]} ({d[1]})" for d in timing.get("best_days", [])[:3])
    best_hours_str = ", ".join(f"{h[0]:02d}:00" for h in timing.get("best_hours", [])[:3])
    st.caption(f"**Best days:** {best_days_str}  ·  **Best hours (UTC):** {best_hours_str}")
else:
    st.info("Not enough post history to build the heatmap yet. Post and collect for a few weeks.")

st.divider()

# ------------------------------------------------------------------
# Row 5 & 6: Top posts + underperforming posts
# ------------------------------------------------------------------

col_top, col_bottom = st.columns(2)

with col_top:
    st.subheader("🏆 Top Posts")
    if top:
        top_df = pd.DataFrame([
            {
                "ER %": round(p.get("engagement_rate", 0), 2),
                "Caption": (p.get("caption") or p.get("title") or "—")[:60],
                "Likes": p.get("likes", 0),
                "Comments": p.get("comments", 0),
                "Shares": p.get("shares", 0),
                "Link": p.get("permalink") or p.get("share_url") or "",
            }
            for p in top
        ])
        st.dataframe(
            top_df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Link": st.column_config.LinkColumn("Link", display_text="Open"),
            },
        )
    else:
        st.info("No posts in this window.")

with col_bottom:
    st.subheader("📉 Needs Improvement")
    if bottom:
        bot_df = pd.DataFrame([
            {
                "ER %": round(p.get("engagement_rate", 0), 2),
                "Caption": (p.get("caption") or p.get("title") or "—")[:60],
                "Likes": p.get("likes", 0),
                "Comments": p.get("comments", 0),
                "Link": p.get("permalink") or p.get("share_url") or "",
            }
            for p in bottom
        ])
        st.dataframe(
            bot_df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Link": st.column_config.LinkColumn("Link", display_text="Open"),
            },
        )
    else:
        st.info("No posts in this window.")

st.divider()

# ------------------------------------------------------------------
# Row 7: Engagement over time per post
# ------------------------------------------------------------------

st.subheader("Engagement Rate per Post Over Time")

if posts:
    post_df = pd.DataFrame(posts)
    post_df["posted_at"] = pd.to_datetime(post_df["posted_at"], errors="coerce")
    post_df = post_df.dropna(subset=["posted_at"]).sort_values("posted_at")
    post_df["engagement_rate"] = post_df.apply(
        lambda r: anlyt.engagement_rate(r.to_dict(), followers), axis=1
    )
    post_df["label"] = post_df.apply(
        lambda r: (r.get("caption") or r.get("title") or r.get("id") or "")[:30], axis=1
    )

    fig_er = go.Figure()
    fig_er.add_trace(go.Scatter(
        x=post_df["posted_at"],
        y=post_df["engagement_rate"],
        mode="markers+lines",
        marker=dict(
            size=10,
            color=post_df["engagement_rate"],
            colorscale="Viridis",
            showscale=True,
            colorbar=dict(title="ER %"),
        ),
        text=post_df["label"],
        hovertemplate="<b>%{text}</b><br>Date: %{x|%b %d}<br>ER: %{y:.2f}%<extra></extra>",
        line=dict(color="rgba(100,100,100,0.3)", width=1),
    ))
    fig_er.update_layout(
        height=300,
        margin=dict(l=0, r=0, t=10, b=0),
        xaxis_title=None,
        yaxis_title="Engagement Rate (%)",
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(showgrid=False),
        yaxis=dict(gridcolor="rgba(150,150,150,0.1)"),
    )
    st.plotly_chart(fig_er, use_container_width=True)

st.divider()

# ------------------------------------------------------------------
# Row 8: Recommendations
# ------------------------------------------------------------------

st.subheader("💡 Recommendations")

if recs:
    for rec in recs:
        st.markdown(f"- {rec}")
else:
    st.info("Collect more data to generate recommendations.")

st.markdown("<br><br>", unsafe_allow_html=True)
st.caption("Social Media Analytics · Auto-refreshes every 5 min · Built with Streamlit + Plotly")
