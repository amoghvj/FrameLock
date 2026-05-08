"""
Search Module
- Retrieves candidate videos from YouTube based on the input video's title
- Uses YouTube Data API v3 (interim hotfix — replaces yt-dlp search)

[INTERIM HOTFIX] Replaced yt-dlp search with YouTube Data API v3
to avoid bot detection on Cloud Run.
"""

import os
import requests as _http

# import yt_dlp  # [INTERIM HOTFIX] Commented out — using YouTube Data API v3 instead


class SearchService:
    """Searches YouTube for candidate videos to compare against."""

    def __init__(self, max_results: int = 10):
        """
        Initialize SearchService.

        Args:
            max_results: Maximum number of candidate videos to retrieve.
        """
        self.max_results = max_results

    def search_videos(self, query: str) -> list:
        """
        Search YouTube for videos matching the query via YouTube Data API v3.

        [INTERIM HOTFIX] Replaces yt-dlp search to avoid bot detection on Cloud Run.

        Args:
            query: Search query string (typically the input video's title).

        Returns:
            List of dicts with keys: 'id', 'title', 'uploader', 'url', 'thumbnail_url'
        """
        api_key = os.environ.get("YOUTUBE_API_KEY")
        if not api_key:
            print("  ✗ YOUTUBE_API_KEY not set, cannot search")
            return []

        # ── OLD: yt-dlp search (commented out, not deleted) ──────────────
        # search_url = f"ytsearch{self.max_results}:{query}"
        # ydl_opts = {
        #     "quiet": True,
        #     "no_warnings": True,
        #     "extract_flat": True,
        #     "skip_download": True,
        # }
        # try:
        #     with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        #         result = ydl.extract_info(search_url, download=False)
        #     candidates = []
        #     entries = result.get("entries", [])
        #     for entry in entries:
        #         if entry is None:
        #             continue
        #         video_id = entry.get("id", "")
        #         title = entry.get("title", "Unknown")
        #         uploader = entry.get("uploader", "Unknown Channel")
        #         thumbnail_url = entry.get("thumbnail") or entry.get("thumbnails", [{}])[0].get("url", "")
        #         if not thumbnail_url and video_id:
        #             thumbnail_url = f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg"
        #         candidates.append({
        #             "id": video_id,
        #             "title": title,
        #             "uploader": uploader,
        #             "url": f"https://www.youtube.com/watch?v={video_id}",
        #             "thumbnail_url": thumbnail_url,
        #         })
        #     print(f"  ✓ Found {len(candidates)} candidate videos")
        #     return candidates
        # except Exception as e:
        #     print(f"  ✗ Search failed: {e}")
        #     return []
        # ── END OLD ──────────────────────────────────────────────────────

        # ── NEW: YouTube Data API v3 search ──────────────────────────────
        try:
            resp = _http.get(
                "https://www.googleapis.com/youtube/v3/search",
                params={
                    "q": query,
                    "type": "video",
                    "part": "snippet",
                    "maxResults": self.max_results,
                    "key": api_key,
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()

            candidates = []
            for item in data.get("items", []):
                video_id = item.get("id", {}).get("videoId", "")
                if not video_id:
                    continue

                snippet = item.get("snippet", {})

                # Select best available thumbnail
                thumbnail_url = ""
                thumbnails = snippet.get("thumbnails", {})
                for quality in ("high", "medium", "default"):
                    if quality in thumbnails:
                        thumbnail_url = thumbnails[quality].get("url", "")
                        break
                if not thumbnail_url and video_id:
                    thumbnail_url = f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg"

                candidates.append({
                    "id": video_id,
                    "title": snippet.get("title", "Unknown"),
                    "uploader": snippet.get("channelTitle", "Unknown Channel"),
                    "url": f"https://www.youtube.com/watch?v={video_id}",
                    "thumbnail_url": thumbnail_url,
                })

            print(f"  ✓ Found {len(candidates)} candidate videos")
            return candidates

        except Exception as e:
            print(f"  ✗ Search failed: {e}")
            return []
