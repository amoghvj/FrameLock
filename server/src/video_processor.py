"""
Video Processing Module
- Fetches video metadata via YouTube Data API v3 (interim hotfix)
- Extracts N frames by seeking directly to calculated timestamps using FFmpeg
- No full video download required — grabs only the bytes needed for each frame

[INTERIM HOTFIX] get_video_info() uses YouTube Data API v3 instead of yt-dlp
to avoid bot detection on Cloud Run. Frame extraction code is preserved
but unused since the UI sends frames=0 (thumbnail-only mode).
"""

import io
import os
import subprocess
import tempfile
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
import re
import requests as _http

import yt_dlp
from PIL import Image


def _get_ffmpeg_path() -> str:
    """
    Locate a usable ffmpeg binary.
    Tries system PATH first, then falls back to imageio-ffmpeg's bundled binary.
    """
    # 1. Try system ffmpeg
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True, timeout=5,
        )
        if result.returncode == 0:
            return "ffmpeg"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # 2. Try imageio-ffmpeg bundled binary
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        pass

    raise RuntimeError(
        "FFmpeg not found. Install it system-wide or run: pip install imageio-ffmpeg"
    )


def _parse_iso8601_duration(duration_str: str) -> float:
    """
    Parse ISO 8601 duration string (e.g., 'PT4M13S') to seconds.
    Used to convert YouTube Data API contentDetails.duration to numeric seconds.
    """
    match = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', duration_str)
    if not match:
        return 0.0
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    return float(hours * 3600 + minutes * 60 + seconds)


class VideoProcessor:
    """
    Handles video metadata retrieval and frame extraction.

    Architecture:
        1. get_video_info()  — Fetches metadata + stream URL (no download)
        2. extract_frames()  — Uses FFmpeg to seek to specific timestamps
                               and grab individual frames from the stream
    """

    def __init__(self):
        self.ffmpeg_path = _get_ffmpeg_path()

    @staticmethod
    def _clean_youtube_url(url: str) -> str:
        """
        Strip non-essential query parameters from a YouTube URL.
        Keeps only 'v' (video ID) and 't' (timestamp). Removes comment
        links (&lc=), playlist params (&list=, &index=), tracking (&si=), etc.
        """
        parsed = urlparse(url)
        params = parse_qs(parsed.query)

        clean_params = {}
        for key in ("v", "t"):
            if key in params:
                clean_params[key] = params[key][0]

        clean_query = urlencode(clean_params)
        return urlunparse(parsed._replace(query=clean_query))

    @staticmethod
    def _extract_video_id(url: str) -> str:
        """Extract the video ID from various YouTube URL formats."""
        parsed = urlparse(url)
        if parsed.hostname in ("youtu.be",):
            return parsed.path.lstrip("/").split("/")[0]
        if parsed.hostname in ("www.youtube.com", "youtube.com", "m.youtube.com"):
            if parsed.path == "/watch":
                return parse_qs(parsed.query).get("v", [""])[0]
            if parsed.path.startswith(("/embed/", "/shorts/", "/v/")):
                return parsed.path.split("/")[2]
        # Fallback: try regex for 11-char YouTube video ID
        match = re.search(r"(?:v=|/)([a-zA-Z0-9_-]{11})", url)
        return match.group(1) if match else ""

    def get_video_info(self, url: str) -> dict:
        """
        Fetch video metadata via YouTube Data API v3.

        [INTERIM HOTFIX] Replaces yt-dlp extraction with YouTube Data API
        to avoid bot detection on Cloud Run. stream_url is set to empty
        string since the API does not provide direct stream URLs.
        The frame extraction code path is unused (UI sends frames=0).

        Args:
            url: YouTube video URL.

        Returns:
            dict with keys:
                'title', 'id', 'duration' (seconds), 'stream_url', 'url', 'thumbnail', 'uploader'

        Raises:
            RuntimeError: If metadata extraction fails.
        """
        url = self._clean_youtube_url(url)
        video_id = self._extract_video_id(url)
        if not video_id:
            raise RuntimeError(f"Could not extract video ID from URL: {url}")

        api_key = os.environ.get("YOUTUBE_API_KEY")
        if not api_key:
            raise RuntimeError("YOUTUBE_API_KEY environment variable is not set.")

        # ── OLD: yt-dlp extraction (commented out, not deleted) ──────────
        # ydl_opts = {
        #     "format": "worst[ext=mp4]",
        #     "quiet": True,
        #     "no_warnings": True,
        #     "skip_download": True,
        # }
        # try:
        #     with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        #         info = ydl.extract_info(url, download=False)
        #         stream_url = info.get("url", "")
        #         if not stream_url:
        #             formats = info.get("requested_formats", [])
        #             if formats:
        #                 stream_url = formats[0].get("url", "")
        #         if not stream_url:
        #             raise RuntimeError("Could not obtain a direct stream URL.")
        #         return {
        #             "title": info.get("title", "Unknown"),
        #             "id": info.get("id", ""),
        #             "duration": info.get("duration", 0),
        #             "stream_url": stream_url,
        #             "url": url,
        #             "thumbnail": info.get("thumbnail", ""),
        #             "uploader": info.get("uploader", "Unknown Channel"),
        #         }
        # except Exception as e:
        #     raise RuntimeError(f"Failed to get video info: {e}")
        # ── END OLD ──────────────────────────────────────────────────────

        # ── NEW: YouTube Data API v3 ─────────────────────────────────────
        try:
            resp = _http.get(
                "https://www.googleapis.com/youtube/v3/videos",
                params={
                    "id": video_id,
                    "part": "snippet,contentDetails",
                    "key": api_key,
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()

            items = data.get("items", [])
            if not items:
                raise RuntimeError(f"No video found for ID: {video_id}")

            item = items[0]
            snippet = item["snippet"]
            duration_iso = item["contentDetails"]["duration"]
            duration_secs = _parse_iso8601_duration(duration_iso)

            # Select best available thumbnail
            thumbnail_url = ""
            thumbnails = snippet.get("thumbnails", {})
            for quality in ("high", "medium", "default"):
                if quality in thumbnails:
                    thumbnail_url = thumbnails[quality].get("url", "")
                    break

            return {
                "title": snippet.get("title", "Unknown"),
                "id": video_id,
                "duration": duration_secs,
                "stream_url": "",  # Not available via API — frame extraction disabled in UI
                "url": url,
                "thumbnail": thumbnail_url,
                "uploader": snippet.get("channelTitle", "Unknown Channel"),
            }
        except _http.RequestException as e:
            raise RuntimeError(f"YouTube API request failed: {e}")
        except (KeyError, IndexError) as e:
            raise RuntimeError(f"Failed to parse YouTube API response: {e}")

    def calculate_frame_timestamps(self, duration: float, n_frames: int) -> list:
        """
        Calculate evenly spaced timestamps for frame extraction.

        Skips the first and last 5% of the video to avoid intros/outros.
        Divides the effective length by (n_frames + 1) so frames are
        evenly distributed with equal gaps at both ends.

        Args:
            duration: Total video duration in seconds.
            n_frames: Number of frames to extract.

        Returns:
            List of timestamps (in seconds) to extract frames at.
        """
        if n_frames <= 0:
            return []

        # Define effective region (skip 5% at start and end)
        effective_start = duration * 0.05
        effective_end = duration * 0.95

        # For very short videos, use the full duration
        if effective_end <= effective_start:
            effective_start = 0
            effective_end = duration

        effective_length = effective_end - effective_start

        # Divide by (n + 1) so frames are evenly spaced with equal
        # gaps before the first and after the last frame
        offset = effective_length / (n_frames + 1)

        timestamps = [
            round(effective_start + offset * (i + 1), 2)
            for i in range(n_frames)
        ]

        return timestamps

    def _grab_frame_at_timestamp(self, stream_url: str, timestamp: float) -> Image.Image:
        """
        Use FFmpeg to seek to a specific timestamp in the stream and grab one frame.

        The -ss flag BEFORE -i makes FFmpeg seek using byte-range requests
        (input seeking), which is extremely fast because it jumps directly
        to the nearest keyframe without downloading everything before it.

        Args:
            stream_url: Direct video stream URL.
            timestamp: Time in seconds to grab the frame at.

        Returns:
            PIL Image of the grabbed frame.

        Raises:
            RuntimeError: If FFmpeg fails to grab the frame.
        """
        cmd = [
            self.ffmpeg_path,
            "-ss", str(timestamp),       # Seek BEFORE input (fast input seeking)
            "-i", stream_url,            # Stream URL
            "-frames:v", "1",            # Grab exactly 1 frame
            "-f", "image2pipe",          # Pipe output as image
            "-vcodec", "png",            # Output as PNG
            "-loglevel", "error",        # Only show errors
            "pipe:1",                    # Write to stdout
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                stdin=subprocess.DEVNULL,
                timeout=30,
            )

            if result.returncode != 0:
                error_msg = result.stderr.decode("utf-8", errors="replace").strip()
                raise RuntimeError(f"FFmpeg error at {timestamp}s: {error_msg}")

            if not result.stdout:
                raise RuntimeError(f"FFmpeg returned no data at {timestamp}s")

            image = Image.open(io.BytesIO(result.stdout)).convert("RGB")
            return image

        except subprocess.TimeoutExpired:
            raise RuntimeError(f"FFmpeg timed out seeking to {timestamp}s")

    def extract_frames(self, stream_url: str, duration: float, n_frames: int = 3) -> list:
        """
        Extract N frames from a video stream by seeking to calculated timestamps.

        No full download is performed — each frame is grabbed individually
        by seeking directly to the target timestamp in the remote stream.

        Args:
            stream_url: Direct video stream URL (from get_video_info).
            duration: Video duration in seconds (from get_video_info).
            n_frames: Number of frames to extract (default 3).

        Returns:
            List of PIL Image objects.

        Raises:
            RuntimeError: If no frames could be extracted.
        """
        timestamps = self.calculate_frame_timestamps(duration, n_frames)

        print(f"  Timestamps to extract: {timestamps}")

        frames = []
        for i, ts in enumerate(timestamps):
            try:
                frame = self._grab_frame_at_timestamp(stream_url, ts)
                frames.append(frame)
                print(f"  ✓ Frame {i+1}/{n_frames} grabbed at {ts:.1f}s")
            except RuntimeError as e:
                print(f"  ✗ Frame {i+1}/{n_frames} failed at {ts:.1f}s: {e}")
                continue

        if not frames:
            raise RuntimeError("Failed to extract any frames from the video.")

        print(f"  ✓ Extracted {len(frames)}/{n_frames} frames total")
        return frames
