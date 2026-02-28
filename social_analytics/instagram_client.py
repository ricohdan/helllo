"""
Instagram Graph API client.

Requires a Business or Creator account connected to a Facebook Page.
Docs: https://developers.facebook.com/docs/instagram-api
"""

import os
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
import requests

logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.instagram.com/v19.0"


class InstagramClient:
    def __init__(self):
        self.access_token = os.environ["INSTAGRAM_ACCESS_TOKEN"]
        self.user_id = os.environ["INSTAGRAM_USER_ID"]

    def _get(self, path: str, params: dict = None) -> dict:
        params = params or {}
        params["access_token"] = self.access_token
        url = f"{GRAPH_BASE}/{path}"
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Account-level metrics
    # ------------------------------------------------------------------

    def get_account_info(self) -> dict:
        """Return basic profile info and current follower count."""
        data = self._get(
            self.user_id,
            {"fields": "id,username,name,biography,followers_count,media_count"},
        )
        return data

    def get_account_insights(self, since: datetime, until: datetime) -> dict:
        """
        Fetch account-level insights for a date range.
        Available metrics: impressions, reach, profile_views, website_clicks,
        follower_count (daily delta), accounts_engaged.
        """
        metrics = [
            "impressions",
            "reach",
            "profile_views",
            "website_clicks",
            "accounts_engaged",
        ]
        params = {
            "metric": ",".join(metrics),
            "period": "day",
            "since": int(since.timestamp()),
            "until": int(until.timestamp()),
        }
        data = self._get(f"{self.user_id}/insights", params)
        return data.get("data", [])

    def get_follower_demographics(self) -> dict:
        """Fetch follower age/gender and top cities/countries breakdown."""
        demographic_metrics = [
            "follower_demographics",
        ]
        params = {
            "metric": ",".join(demographic_metrics),
            "period": "lifetime",
            "metric_type": "total_value",
            "breakdown": "age,gender",
        }
        try:
            return self._get(f"{self.user_id}/insights", params)
        except Exception as e:
            logger.warning("Could not fetch demographics: %s", e)
            return {}

    # ------------------------------------------------------------------
    # Media (posts) metrics
    # ------------------------------------------------------------------

    def get_recent_media(self, limit: int = 50) -> list[dict]:
        """
        Return recent posts with basic fields.
        Fields available without extra permissions:
        id, caption, media_type, timestamp, like_count, comments_count, permalink.
        """
        fields = (
            "id,caption,media_type,timestamp,like_count,comments_count,permalink"
        )
        data = self._get(
            f"{self.user_id}/media",
            {"fields": fields, "limit": limit},
        )
        return data.get("data", [])

    def get_media_insights(self, media_id: str, media_type: str) -> dict:
        """
        Fetch engagement insights for a single post.
        Metrics vary by media type (IMAGE/VIDEO/CAROUSEL_ALBUM/REELS).
        """
        if media_type == "VIDEO" or media_type == "REELS":
            metrics = [
                "impressions",
                "reach",
                "likes",
                "comments",
                "shares",
                "saved",
                "plays",
                "total_interactions",
            ]
        else:
            metrics = [
                "impressions",
                "reach",
                "likes",
                "comments",
                "shares",
                "saved",
                "total_interactions",
            ]

        try:
            data = self._get(
                f"{media_id}/insights",
                {"metric": ",".join(metrics)},
            )
            result = {}
            for item in data.get("data", []):
                result[item["name"]] = item["values"][0]["value"] if item.get("values") else item.get("value", 0)
            return result
        except Exception as e:
            logger.warning("Could not fetch insights for media %s: %s", media_id, e)
            return {}

    def collect_posts_with_insights(self, days_back: int = 30) -> list[dict]:
        """
        Return recent posts enriched with per-post insight metrics.
        Only includes posts published within the last `days_back` days.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)
        posts = self.get_recent_media(limit=100)

        enriched = []
        for post in posts:
            posted_at = datetime.fromisoformat(post["timestamp"].replace("Z", "+00:00"))
            if posted_at < cutoff:
                continue

            insights = self.get_media_insights(post["id"], post.get("media_type", "IMAGE"))
            post["insights"] = insights
            post["posted_at"] = posted_at
            enriched.append(post)
            logger.debug("Fetched insights for post %s", post["id"])

        logger.info("Collected %d Instagram posts with insights", len(enriched))
        return enriched
