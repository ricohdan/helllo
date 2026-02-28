"""
Report delivery module.

Supports two channels:
  1. Email (SMTP) — sends an HTML email with the weekly digest
  2. Slack — sends a structured Block Kit message via Incoming Webhook
"""

from __future__ import annotations

import logging
import os
import smtplib
import json
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

import requests

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Report data structure (passed by main.py)
# ------------------------------------------------------------------

class PlatformReport:
    """Holds all analytics results for one platform."""
    def __init__(
        self,
        platform: str,
        username: str,
        followers: int,
        growth: dict,
        top_posts: list[dict],
        bottom_posts: list[dict],
        type_breakdown: dict,
        timing: dict,
        wow: dict,
        recommendations: list[str],
    ):
        self.platform = platform
        self.username = username
        self.followers = followers
        self.growth = growth
        self.top_posts = top_posts
        self.bottom_posts = bottom_posts
        self.type_breakdown = type_breakdown
        self.timing = timing
        self.wow = wow
        self.recommendations = recommendations


# ------------------------------------------------------------------
# Email reporter
# ------------------------------------------------------------------

def send_email_report(reports: list[PlatformReport]):
    if os.environ.get("EMAIL_ENABLED", "false").lower() != "true":
        logger.info("Email reporting disabled.")
        return

    recipients = [r.strip() for r in os.environ.get("REPORT_RECIPIENTS", "").split(",") if r.strip()]
    if not recipients:
        logger.warning("No REPORT_RECIPIENTS configured — skipping email.")
        return

    subject = f"Social Media Weekly Report — {datetime.now(timezone.utc).strftime('%b %d, %Y')}"
    html = _build_html(reports)
    text = _build_text(reports)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = os.environ["SMTP_USER"]
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(text, "plain"))
    msg.attach(MIMEText(html, "html"))

    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ["SMTP_USER"]
    password = os.environ["SMTP_PASSWORD"]

    with smtplib.SMTP(host, port) as server:
        server.starttls()
        server.login(user, password)
        server.sendmail(user, recipients, msg.as_string())

    logger.info("Email report sent to %s", recipients)


def _build_html(reports: list[PlatformReport]) -> str:
    sections = ""
    for r in reports:
        growth = r.growth
        wow_this = r.wow.get("this_week", {})
        wow_last = r.wow.get("last_week", {})

        delta_str = ""
        if growth.get("delta") is not None:
            sign = "+" if growth["delta"] >= 0 else ""
            delta_str = f"{sign}{growth['delta']:,} ({sign}{growth.get('delta_pct', 0)}%)"

        top_rows = ""
        for i, p in enumerate(r.top_posts, 1):
            er = p.get("engagement_rate", 0)
            caption = (p.get("caption") or p.get("title") or "—")[:60]
            link = p.get("permalink") or p.get("share_url") or "#"
            top_rows += f"""
            <tr>
              <td style="padding:6px">{i}</td>
              <td style="padding:6px"><a href="{link}">{caption}…</a></td>
              <td style="padding:6px">{er:.2f}%</td>
              <td style="padding:6px">{p.get('likes',0):,}</td>
              <td style="padding:6px">{p.get('comments',0):,}</td>
              <td style="padding:6px">{p.get('shares',0):,}</td>
            </tr>"""

        type_rows = ""
        for mtype, stats in r.type_breakdown.items():
            type_rows += f"""
            <tr>
              <td style="padding:6px">{mtype}</td>
              <td style="padding:6px">{stats['count']}</td>
              <td style="padding:6px">{stats['avg_likes']:,}</td>
              <td style="padding:6px">{stats['avg_comments']:,}</td>
              <td style="padding:6px">{stats['avg_shares']:,}</td>
            </tr>"""

        rec_items = "".join(f"<li>{rec}</li>" for rec in r.recommendations)
        best_day = r.timing.get("best_days", [["—"]])[0][0]
        best_hour = r.timing.get("best_hours", [[0]])[0][0]

        sections += f"""
        <div style="font-family:Arial,sans-serif;max-width:700px;margin:0 auto 40px">
          <h2 style="background:#111;color:#fff;padding:16px;border-radius:6px">
            {r.platform.upper()} — @{r.username}
          </h2>

          <h3>Follower Growth</h3>
          <table style="border-collapse:collapse;width:100%">
            <tr>
              <td style="padding:8px;background:#f5f5f5;width:40%"><strong>Current followers</strong></td>
              <td style="padding:8px">{r.followers:,}</td>
            </tr>
            <tr>
              <td style="padding:8px;background:#f5f5f5"><strong>Week-over-week change</strong></td>
              <td style="padding:8px">{delta_str or '—'}</td>
            </tr>
          </table>

          <h3>This Week vs Last Week</h3>
          <table style="border-collapse:collapse;width:100%;border:1px solid #ddd">
            <thead><tr style="background:#eee">
              <th style="padding:8px">Metric</th>
              <th style="padding:8px">This week</th>
              <th style="padding:8px">Last week</th>
            </tr></thead>
            <tbody>
              <tr>
                <td style="padding:8px">Posts published</td>
                <td style="padding:8px">{wow_this.get('posts',0)}</td>
                <td style="padding:8px">{wow_last.get('posts',0)}</td>
              </tr>
              <tr>
                <td style="padding:8px">Avg engagement rate</td>
                <td style="padding:8px">{wow_this.get('avg_engagement_rate',0):.2f}%</td>
                <td style="padding:8px">{wow_last.get('avg_engagement_rate',0):.2f}%</td>
              </tr>
              <tr>
                <td style="padding:8px">Total reach</td>
                <td style="padding:8px">{wow_this.get('total_reach',0):,}</td>
                <td style="padding:8px">{wow_last.get('total_reach',0):,}</td>
              </tr>
              <tr>
                <td style="padding:8px">Total likes</td>
                <td style="padding:8px">{wow_this.get('total_likes',0):,}</td>
                <td style="padding:8px">{wow_last.get('total_likes',0):,}</td>
              </tr>
            </tbody>
          </table>

          <h3>Top 5 Posts This Period</h3>
          <table style="border-collapse:collapse;width:100%;border:1px solid #ddd">
            <thead><tr style="background:#eee">
              <th style="padding:8px">#</th>
              <th style="padding:8px">Caption</th>
              <th style="padding:8px">ER%</th>
              <th style="padding:8px">Likes</th>
              <th style="padding:8px">Comments</th>
              <th style="padding:8px">Shares</th>
            </tr></thead>
            <tbody>{top_rows}</tbody>
          </table>

          <h3>Content Type Breakdown</h3>
          <table style="border-collapse:collapse;width:100%;border:1px solid #ddd">
            <thead><tr style="background:#eee">
              <th style="padding:8px">Type</th>
              <th style="padding:8px">Count</th>
              <th style="padding:8px">Avg Likes</th>
              <th style="padding:8px">Avg Comments</th>
              <th style="padding:8px">Avg Shares</th>
            </tr></thead>
            <tbody>{type_rows}</tbody>
          </table>

          <h3>Best Posting Times</h3>
          <p><strong>Best day:</strong> {best_day} &nbsp;|&nbsp;
             <strong>Best hour:</strong> {best_hour:02d}:00 UTC</p>

          <h3>Recommendations</h3>
          <ul style="line-height:1.8">{rec_items}</ul>
          <hr style="margin-top:40px;border:none;border-top:1px solid #eee">
        </div>
        """

    return f"""<!DOCTYPE html><html><body>
    <h1 style="font-family:Arial;text-align:center;color:#333">
      Weekly Social Media Report
    </h1>
    <p style="font-family:Arial;text-align:center;color:#888">
      {datetime.now(timezone.utc).strftime('%A, %B %d, %Y')} · Generated automatically
    </p>
    {sections}
    </body></html>"""


def _build_text(reports: list[PlatformReport]) -> str:
    lines = [
        f"WEEKLY SOCIAL MEDIA REPORT — {datetime.now(timezone.utc).strftime('%b %d, %Y')}",
        "=" * 60,
    ]
    for r in reports:
        growth = r.growth
        delta_str = ""
        if growth.get("delta") is not None:
            sign = "+" if growth["delta"] >= 0 else ""
            delta_str = f"{sign}{growth['delta']:,} ({sign}{growth.get('delta_pct',0)}%)"

        lines += [
            f"\n{r.platform.upper()} — @{r.username}",
            "-" * 40,
            f"Followers: {r.followers:,}   Change: {delta_str or 'n/a'}",
            "",
            "TOP POSTS:",
        ]
        for i, p in enumerate(r.top_posts, 1):
            caption = (p.get("caption") or p.get("title") or "—")[:70]
            lines.append(f"  {i}. [{p.get('engagement_rate',0):.2f}% ER] {caption}")

        lines.append("\nRECOMMENDATIONS:")
        for rec in r.recommendations:
            lines.append(f"  • {rec}")

    return "\n".join(lines)


# ------------------------------------------------------------------
# Slack reporter
# ------------------------------------------------------------------

def send_slack_report(reports: list[PlatformReport]):
    if os.environ.get("SLACK_ENABLED", "false").lower() != "true":
        logger.info("Slack reporting disabled.")
        return

    webhook_url = os.environ.get("SLACK_WEBHOOK_URL", "")
    if not webhook_url:
        logger.warning("SLACK_WEBHOOK_URL not set — skipping Slack report.")
        return

    blocks = _build_slack_blocks(reports)
    payload = {"blocks": blocks}
    resp = requests.post(webhook_url, json=payload, timeout=15)
    if resp.status_code != 200:
        logger.error("Slack webhook returned %s: %s", resp.status_code, resp.text)
    else:
        logger.info("Slack report sent.")


def _build_slack_blocks(reports: list[PlatformReport]) -> list[dict]:
    date_str = datetime.now(timezone.utc).strftime("%b %d, %Y")
    blocks: list[dict] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"Weekly Social Media Report — {date_str}"},
        },
        {"type": "divider"},
    ]

    for r in reports:
        growth = r.growth
        delta_str = "n/a"
        if growth.get("delta") is not None:
            sign = "+" if growth["delta"] >= 0 else ""
            delta_str = f"{sign}{growth['delta']:,} ({sign}{growth.get('delta_pct', 0)}%)"

        wow_this = r.wow.get("this_week", {})
        best_day = r.timing.get("best_days", [["—"]])[0][0]
        best_hour = r.timing.get("best_hours", [[0]])[0][0]

        top_post_lines = []
        for i, p in enumerate(r.top_posts[:3], 1):
            caption = (p.get("caption") or p.get("title") or "—")[:50]
            link = p.get("permalink") or p.get("share_url") or ""
            er = p.get("engagement_rate", 0)
            if link:
                top_post_lines.append(f"{i}. <{link}|{caption}…> — *{er:.2f}% ER*")
            else:
                top_post_lines.append(f"{i}. {caption}… — *{er:.2f}% ER*")

        rec_lines = "\n".join(f"• {rec}" for rec in r.recommendations)

        blocks += [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*{r.platform.upper()} — @{r.username}*",
                },
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Followers*\n{r.followers:,}"},
                    {"type": "mrkdwn", "text": f"*WoW change*\n{delta_str}"},
                    {"type": "mrkdwn", "text": f"*Avg ER this week*\n{wow_this.get('avg_engagement_rate',0):.2f}%"},
                    {"type": "mrkdwn", "text": f"*Best time to post*\n{best_day} @ {best_hour:02d}:00 UTC"},
                ],
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*Top 3 Posts:*\n" + "\n".join(top_post_lines),
                },
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Recommendations:*\n{rec_lines}"},
            },
            {"type": "divider"},
        ]

    return blocks
