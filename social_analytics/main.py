"""
Social Media Analytics — Main entry point.

Usage:
  python main.py collect          # Pull fresh data from APIs and store it
  python main.py report           # Generate and send the weekly report now
  python main.py run              # Start the scheduler (collect daily, report weekly)
  python main.py show             # Print a quick summary to stdout (no sending)
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

load_dotenv()

import analytics as anlyt
import db
from instagram_client import InstagramClient
from reporter import PlatformReport, send_email_report, send_slack_report
from tiktok_client import TikTokClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("main")


# ------------------------------------------------------------------
# Data collection
# ------------------------------------------------------------------

def collect_instagram():
    logger.info("=== Collecting Instagram data ===")
    client = InstagramClient()

    info = client.get_account_info()
    followers = info.get("followers_count", 0)

    db.upsert_snapshot("instagram", {
        "followers": followers,
        "post_count": info.get("media_count"),
    })

    posts = client.collect_posts_with_insights(days_back=30)
    db.upsert_posts("instagram", posts)
    logger.info("Instagram: %d followers, %d posts collected", followers, len(posts))


def collect_tiktok():
    logger.info("=== Collecting TikTok data ===")
    client = TikTokClient()

    user = client.get_user_info()
    followers = user.get("follower_count", 0)

    db.upsert_snapshot("tiktok", {
        "followers": followers,
        "following": user.get("following_count"),
        "likes_total": user.get("likes_count"),
        "post_count": user.get("video_count"),
    })

    videos = client.collect_videos_with_insights(days_back=30)
    db.upsert_posts("tiktok", videos)
    logger.info("TikTok: %d followers, %d videos collected", followers, len(videos))


def collect_all():
    errors = []

    if os.environ.get("INSTAGRAM_ACCESS_TOKEN"):
        try:
            collect_instagram()
        except Exception as e:
            logger.error("Instagram collection failed: %s", e)
            errors.append(f"Instagram: {e}")
    else:
        logger.info("INSTAGRAM_ACCESS_TOKEN not set — skipping Instagram.")

    if os.environ.get("TIKTOK_ACCESS_TOKEN"):
        try:
            collect_tiktok()
        except Exception as e:
            logger.error("TikTok collection failed: %s", e)
            errors.append(f"TikTok: {e}")
    else:
        logger.info("TIKTOK_ACCESS_TOKEN not set — skipping TikTok.")

    if errors:
        logger.warning("Collection finished with errors: %s", errors)

    db.purge_old_data(int(os.environ.get("DATA_RETENTION_DAYS", 365)))


# ------------------------------------------------------------------
# Report generation
# ------------------------------------------------------------------

def _build_platform_report(platform: str, days_back: int = 30) -> PlatformReport | None:
    posts = db.get_posts(platform, days_back=days_back)
    snapshots = db.get_snapshots(platform, limit=60)

    if not snapshots:
        logger.warning("No snapshots for %s — skipping report.", platform)
        return None

    latest_snap = max(snapshots, key=lambda s: s["captured_at"])
    followers = latest_snap.get("followers") or 1  # avoid /0

    username = latest_snap.get("username") or platform

    # Split posts into this-week / last-week buckets
    now = datetime.now(timezone.utc)
    one_week_ago = now - timedelta(days=7)
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

    growth = anlyt.follower_growth(snapshots)
    top = anlyt.top_posts(posts, followers, n=5)
    bottom = anlyt.bottom_posts(posts, followers, n=3)
    type_breakdown = anlyt.content_type_breakdown(posts)
    timing = anlyt.best_posting_times(posts)
    wow = anlyt.week_over_week_summary(this_week_posts, last_week_posts, followers)
    recs = anlyt.generate_recommendations(timing, type_breakdown, growth, wow)

    return PlatformReport(
        platform=platform,
        username=username,
        followers=followers,
        growth=growth,
        top_posts=top,
        bottom_posts=bottom,
        type_breakdown=type_breakdown,
        timing=timing,
        wow=wow,
        recommendations=recs,
    )


def generate_report():
    logger.info("=== Generating report ===")
    platforms = []
    if os.environ.get("INSTAGRAM_ACCESS_TOKEN"):
        platforms.append("instagram")
    if os.environ.get("TIKTOK_ACCESS_TOKEN"):
        platforms.append("tiktok")

    reports = []
    for platform in platforms:
        report = _build_platform_report(platform)
        if report:
            reports.append(report)

    if not reports:
        logger.warning("No data available to report.")
        return

    send_email_report(reports)
    send_slack_report(reports)
    logger.info("Report generation complete.")


# ------------------------------------------------------------------
# Quick console summary (no sending)
# ------------------------------------------------------------------

def show_summary():
    platforms = []
    if os.environ.get("INSTAGRAM_ACCESS_TOKEN"):
        platforms.append("instagram")
    if os.environ.get("TIKTOK_ACCESS_TOKEN"):
        platforms.append("tiktok")

    for platform in platforms:
        report = _build_platform_report(platform)
        if not report:
            print(f"\n[{platform.upper()}] No data yet — run `python main.py collect` first.\n")
            continue

        g = report.growth
        delta_str = "n/a"
        if g.get("delta") is not None:
            sign = "+" if g["delta"] >= 0 else ""
            delta_str = f"{sign}{g['delta']:,} ({sign}{g.get('delta_pct', 0)}%)"

        print(f"\n{'='*60}")
        print(f"  {platform.upper()} — @{report.username}")
        print(f"{'='*60}")
        print(f"  Followers:       {report.followers:,}")
        print(f"  WoW change:      {delta_str}")
        print(f"  Posts analysed:  {len(db.get_posts(platform))}")
        print()

        print("  TOP 3 POSTS:")
        for i, p in enumerate(report.top_posts[:3], 1):
            caption = (p.get("caption") or p.get("title") or "—")[:55]
            print(f"    {i}. {p.get('engagement_rate', 0):.2f}% ER — {caption}…")

        print()
        print("  CONTENT TYPE PERFORMANCE:")
        for mtype, stats in report.type_breakdown.items():
            print(f"    {mtype:15s}  count={stats['count']}  avg_likes={stats['avg_likes']}  avg_comments={stats['avg_comments']}")

        print()
        best_day = report.timing.get("best_days", [["—"]])[0][0]
        best_hour = report.timing.get("best_hours", [[0]])[0][0]
        print(f"  BEST TIME TO POST:  {best_day}s at {best_hour:02d}:00 UTC")

        print()
        print("  RECOMMENDATIONS:")
        for rec in report.recommendations:
            print(f"    • {rec}")

    print()


# ------------------------------------------------------------------
# Scheduler
# ------------------------------------------------------------------

def start_scheduler():
    import schedule
    import time

    report_day = os.environ.get("REPORT_DAY", "mon").lower()
    report_time = os.environ.get("REPORT_TIME", "09:00")

    # Collect data every 6 hours
    schedule.every(6).hours.do(collect_all)

    # Send weekly report on the configured day/time
    day_fn = getattr(schedule.every(), report_day)
    day_fn.at(report_time).do(lambda: (collect_all(), generate_report()))

    logger.info(
        "Scheduler started. Collecting every 6h. Report: every %s at %s.",
        report_day.capitalize(),
        report_time,
    )

    # Run collection immediately on startup
    collect_all()

    while True:
        schedule.run_pending()
        time.sleep(60)


# ------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------

def main():
    db.init_db()

    cmd = sys.argv[1] if len(sys.argv) > 1 else "show"

    if cmd == "collect":
        collect_all()
    elif cmd == "report":
        generate_report()
    elif cmd == "run":
        start_scheduler()
    elif cmd == "show":
        show_summary()
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
