"""YouTube URL resolution.

Thin wrapper around `tiktok_uploader.Video.Video.get_youtube_video` so the
immediate upload endpoint and the scheduler can both resolve a URL to a local
file without duplicating yt-dlp wiring. Tests patch `download` to return a
fixture path.
"""
from __future__ import annotations

import os

from tiktok_uploader.Config import Config
from tiktok_uploader.Video import Video


def download(url: str) -> str:
    """Download a YouTube URL to the configured videos_dir and return the
    resulting absolute path. Uses the same yt-dlp options as the CLI."""
    # Video.__init__ performs the download if it detects a YouTube URL,
    # then blocks until the file exists on disk.
    v = Video(url, video_text="")
    abs_path = v.source_ref
    if not os.path.isabs(abs_path):
        abs_path = os.path.join(os.getcwd(), Config.get().videos_dir, abs_path)
    return abs_path
