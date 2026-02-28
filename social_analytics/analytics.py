"""
Analytics engine.

Takes raw posts and snapshots from the DB and produces:
  - Engagement rate per post and overall
  - Best performing posts
  - Best posting times (day-of-week × hour heatmap)
  - Content type breakdown
  - Follower growth trend
  - Week-over-week summary
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# Weights used when scoring a post for the "top posts" ranking.
# Adjust these to reflect what you value most.
ENGAGEMENT_WEIGHTS = {
    "likes": 1,
    "comments": 3,    # comments signal deeper engagement
    "shares": 4,      # shares extend organic reach
    "saves": 2,
    "plays": 0.1,     # normalise high play counts
}


# ------------------------------------------------------------------
# Per-post metrics
# ------------------------------------------------------------------

def engagement_rate(post: dict, followers: int) -> float:
    """
    (likes + comments + shares + saves) / followers * 100
    Returns 0.0 if followers is 0.
    """
    if not followers:
        return 0.0
    interactions = (
        post.get("likes", 0)
        + post.get("comments", 0)
        + post.get("shares", 0)
        + post.get("saves", 0)
    )
    return round(interactions / followers * 100, 4)


def engagement_score(post: dict) -> float:
    """Weighted engagement score used for ranking (independent of follower count)."""
    return sum(
        ENGAGEMENT_WEIGHTS.get(k, 0) * post.get(k, 0)
        for k in ENGAGEMENT_WEIGHTS
    )


# ------------------------------------------------------------------
# Aggregated insights
# ------------------------------------------------------------------

def top_posts(posts: list[dict], followers: int, n: int = 5) -> list[dict]:
    """Return the top `n` posts by engagement rate."""
    enriched = [
        {**p, "engagement_rate": engagement_rate(p, followers)}
        for p in posts
    ]
    return sorted(enriched, key=lambda p: p["engagement_rate"], reverse=True)[:n]


def bottom_posts(posts: list[dict], followers: int, n: int = 3) -> list[dict]:
    """Return the `n` lowest-performing posts (useful for 'what's NOT working')."""
    enriched = [
        {**p, "engagement_rate": engagement_rate(p, followers)}
        for p in posts
    ]
    return sorted(enriched, key=lambda p: p["engagement_rate"])[:n]


def content_type_breakdown(posts: list[dict]) -> dict[str, dict]:
    """
    Group posts by media_type and compute average engagement rate per type.
    Returns: { media_type: { count, avg_er, avg_likes, avg_comments, avg_shares } }
    """
    groups: dict[str, list] = defaultdict(list)
    for p in posts:
        mtype = (p.get("media_type") or "UNKNOWN").upper()
        groups[mtype].append(p)

    summary = {}
    for mtype, items in groups.items():
        count = len(items)
        summary[mtype] = {
            "count": count,
            "avg_likes": _avg(items, "likes"),
            "avg_comments": _avg(items, "comments"),
            "avg_shares": _avg(items, "shares"),
            "avg_saves": _avg(items, "saves"),
            "avg_plays": _avg(items, "plays"),
            "avg_reach": _avg(items, "reach"),
        }
    return summary


def best_posting_times(posts: list[dict]) -> dict:
    """
    Analyse when high-engagement posts were published.

    Returns:
      best_days:  list of (weekday_name, avg_engagement_score) sorted best-first
      best_hours: list of (hour_int, avg_engagement_score) sorted best-first
      heatmap:    dict[weekday][hour] = avg_engagement_score
    """
    day_scores: dict[int, list] = defaultdict(list)
    hour_scores: dict[int, list] = defaultdict(list)
    heatmap: dict[int, dict[int, list]] = defaultdict(lambda: defaultdict(list))

    for p in posts:
        posted_at = p.get("posted_at")
        if not posted_at:
            continue
        if isinstance(posted_at, str):
            posted_at = datetime.fromisoformat(posted_at)
        score = engagement_score(p)
        weekday = posted_at.weekday()   # 0=Mon … 6=Sun
        hour = posted_at.hour
        day_scores[weekday].append(score)
        hour_scores[hour].append(score)
        heatmap[weekday][hour].append(score)

    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    best_days = sorted(
        [(day_names[d], round(_mean(scores), 2)) for d, scores in day_scores.items()],
        key=lambda x: x[1],
        reverse=True,
    )
    best_hours = sorted(
        [(h, round(_mean(scores), 2)) for h, scores in hour_scores.items()],
        key=lambda x: x[1],
        reverse=True,
    )
    heatmap_avg = {
        day_names[d]: {h: round(_mean(s), 2) for h, s in hours.items()}
        for d, hours in heatmap.items()
    }

    return {
        "best_days": best_days,
        "best_hours": best_hours,
        "heatmap": heatmap_avg,
    }


def follower_growth(snapshots: list[dict]) -> dict:
    """
    Compute follower growth metrics from a list of daily snapshots
    (most-recent-first ordering expected from db.get_snapshots).

    Returns: latest, previous_week, delta, delta_pct, 4-week trend list
    """
    if not snapshots:
        return {}

    sorted_snaps = sorted(snapshots, key=lambda s: s["captured_at"])
    latest = sorted_snaps[-1].get("followers") or 0

    # Compare to ~7 days ago
    seven_days_ago = (
        datetime.now(timezone.utc) - timedelta(days=7)
    ).isoformat()
    older = [s for s in sorted_snaps if s["captured_at"] <= seven_days_ago]
    prev_week = older[-1].get("followers") if older else None

    delta = (latest - prev_week) if prev_week is not None else None
    delta_pct = (delta / prev_week * 100) if prev_week else None

    # 4-week weekly buckets (rough)
    weekly: list[Optional[int]] = []
    for weeks_ago in range(4, 0, -1):
        target = (
            datetime.now(timezone.utc) - timedelta(weeks=weeks_ago)
        ).isoformat()
        candidates = [s for s in sorted_snaps if s["captured_at"] <= target]
        weekly.append(candidates[-1].get("followers") if candidates else None)
    weekly.append(latest)

    return {
        "latest": latest,
        "prev_week": prev_week,
        "delta": delta,
        "delta_pct": round(delta_pct, 2) if delta_pct is not None else None,
        "weekly_trend": weekly,  # 5 data points: 4w ago … now
    }


def week_over_week_summary(posts_this_week: list[dict], posts_last_week: list[dict], followers: int) -> dict:
    """Compare this week's posts vs last week's average metrics."""
    def avg_er(posts):
        if not posts:
            return 0.0
        ers = [engagement_rate(p, followers) for p in posts]
        return round(sum(ers) / len(ers), 4)

    def total(posts, field):
        return sum(p.get(field, 0) for p in posts)

    return {
        "this_week": {
            "posts": len(posts_this_week),
            "avg_engagement_rate": avg_er(posts_this_week),
            "total_likes": total(posts_this_week, "likes"),
            "total_comments": total(posts_this_week, "comments"),
            "total_shares": total(posts_this_week, "shares"),
            "total_reach": total(posts_this_week, "reach"),
        },
        "last_week": {
            "posts": len(posts_last_week),
            "avg_engagement_rate": avg_er(posts_last_week),
            "total_likes": total(posts_last_week, "likes"),
            "total_comments": total(posts_last_week, "comments"),
            "total_shares": total(posts_last_week, "shares"),
            "total_reach": total(posts_last_week, "reach"),
        },
    }


def generate_recommendations(
    timing: dict,
    type_breakdown: dict,
    growth: dict,
    wow: dict,
) -> list[str]:
    """
    Turn analytics output into plain-English actionable recommendations.
    Returns a list of recommendation strings.
    """
    recs = []

    # Best day / time
    if timing.get("best_days"):
        top_day = timing["best_days"][0][0]
        recs.append(f"Post on {top_day}s — your audience engages most on that day.")
    if timing.get("best_hours"):
        top_hour = timing["best_hours"][0][0]
        recs.append(
            f"Aim for {top_hour:02d}:00–{top_hour+1:02d}:00 UTC — your highest-engagement hour."
        )

    # Content type
    if type_breakdown:
        best_type = max(
            type_breakdown.items(),
            key=lambda kv: kv[1].get("avg_likes", 0) + kv[1].get("avg_comments", 0),
        )
        recs.append(
            f"{best_type[0].title()} content gets the most interactions — double down on it."
        )

        worst_type = min(
            type_breakdown.items(),
            key=lambda kv: kv[1].get("avg_likes", 0) + kv[1].get("avg_comments", 0),
        )
        if worst_type[0] != best_type[0]:
            recs.append(
                f"{worst_type[0].title()} posts underperform — consider reducing frequency or rethinking the format."
            )

    # Follower growth
    if growth.get("delta") is not None:
        if growth["delta"] > 0:
            recs.append(
                f"You gained {growth['delta']:,} followers this week (+{growth['delta_pct']}%) — keep the cadence up."
            )
        elif growth["delta"] < 0:
            recs.append(
                f"Follower count dropped by {abs(growth['delta']):,} this week — review recent content for quality or posting frequency issues."
            )

    # Week-over-week engagement
    this_er = wow.get("this_week", {}).get("avg_engagement_rate", 0)
    last_er = wow.get("last_week", {}).get("avg_engagement_rate", 0)
    if last_er > 0:
        change = round((this_er - last_er) / last_er * 100, 1)
        direction = "up" if change >= 0 else "down"
        recs.append(
            f"Average engagement rate is {direction} {abs(change)}% vs last week ({this_er:.2f}% vs {last_er:.2f}%)."
        )

    return recs


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------

def _avg(items: list[dict], field: str) -> float:
    vals = [i.get(field, 0) or 0 for i in items]
    return round(sum(vals) / len(vals), 2) if vals else 0.0


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0
