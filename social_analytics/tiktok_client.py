"""
TikTok API client (Content Posting + Research API v2).

Docs: https://developers.tiktok.com/doc/overview
You need a TikTok for Developers app with the required scopes approved:
  - user.info.basic
  - user.info.stats
  - video.list
  - video.insights  (requires approval)
"""

import os
import logging
from datetime import datetime, timedelta, timezone
import requests

logger = logging.getLogger(__name__)

TIKTOK_BASE = "https://open.tiktokapis.com/v2"


class TikTokClient:
    def __init__(self):
        self.client_key = os.environ["TIKTOK_CLIENT_KEY"]
        self.client_secret = os.environ["TIKTOK_CLIENT_SECRET"]
        self._access_token: str = os.environ.get("TIKTOK_ACCESS_TOKEN", "")

    # ------------------------------------------------------------------
    # Auth helpers
    # ------------------------------------------------------------------

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
        }

    def _post(self, path: str, body: dict) -> dict:
        url = f"{TIKTOK_BASE}/{path}"
        resp = requests.post(url, json=body, headers=self._headers(), timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if data.get("error", {}).get("code", "ok") != "ok":
            raise RuntimeError(f"TikTok API error: {data['error']}")
        return data

    def _get(self, path: str, params: dict = None) -> dict:
        url = f"{TIKTOK_BASE}/{path}"
        resp = requests.get(url, params=params, headers=self._headers(), timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if data.get("error", {}).get("code", "ok") != "ok":
            raise RuntimeError(f"TikTok API error: {data['error']}")
        return data

    # ------------------------------------------------------------------
    # Account / user info
    # ------------------------------------------------------------------

    def get_user_info(self) -> dict:
        """Return profile + follower stats for the authenticated user."""
        fields = "open_id,union_id,display_name,avatar_url,follower_count,following_count,likes_count,video_count"
        data = self._get("user/info/", {"fields": fields})
        return data.get("data", {}).get("user", {})

    # ------------------------------------------------------------------
    # Video list + metrics
    # ------------------------------------------------------------------

    def get_videos(self, max_count: int = 20) -> list[dict]:
        """
        Return a list of recent videos with basic fields.
        Paginated automatically up to max_count videos.
        """
        fields = (
            "id,title,create_time,cover_image_url,share_url,"
            "view_count,like_count,comment_count,share_count,play_count"
        )
        videos = []
        cursor = 0
        has_more = True

        while has_more and len(videos) < max_count:
            page_size = min(20, max_count - len(videos))
            body = {
                "max_count": page_size,
                "cursor": cursor,
                "fields": fields,
            }
            data = self._post("video/list/", body)
            page = data.get("data", {})
            videos.extend(page.get("videos", []))
            has_more = page.get("has_more", False)
            cursor = page.get("cursor", 0)

        logger.info("Fetched %d TikTok videos", len(videos))
        return videos[:max_count]

    def get_video_insights(self, video_ids: list[str]) -> dict[str, dict]:
        """
        Fetch detailed analytics for a batch of video IDs.
        Returns a dict keyed by video_id.
        Note: requires video.insights scope (submit for review in dev portal).
        """
        if not video_ids:
            return {}

        fields = (
            "id,view_count,like_count,comment_count,share_count,"
            "average_watch_time,reach,video_views_by_section"
        )
        # API accepts up to 100 IDs per call
        insights: dict[str, dict] = {}
        for i in range(0, len(video_ids), 100):
            batch = video_ids[i : i + 100]
            body = {"filters": {"video_ids": batch}, "fields": fields}
            try:
                data = self._post("video/query/", body)
                for video in data.get("data", {}).get("videos", []):
                    insights[video["id"]] = video
            except Exception as e:
                logger.warning("Could not fetch insights for batch: %s", e)

        return insights

    def collect_videos_with_insights(self, days_back: int = 30) -> list[dict]:
        """
        Return recent videos enriched with detailed analytics.
        Filters to videos posted within `days_back` days.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)
        all_videos = self.get_videos(max_count=100)

        recent = [
            v for v in all_videos
            if datetime.fromtimestamp(v.get("create_time", 0), tz=timezone.utc) >= cutoff
        ]

        video_ids = [v["id"] for v in recent]
        insights = self.get_video_insights(video_ids)

        enriched = []
        for video in recent:
            vid_id = video["id"]
            video["insights"] = insights.get(vid_id, {})
            video["posted_at"] = datetime.fromtimestamp(
                video.get("create_time", 0), tz=timezone.utc
            )
            enriched.append(video)

        logger.info("Collected %d TikTok videos with insights", len(enriched))
        return enriched
