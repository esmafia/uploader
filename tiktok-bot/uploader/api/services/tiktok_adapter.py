"""Thin mockable seam around tiktok_uploader.tiktok.upload_video.

Both the API (immediate uploads) and the scheduler (due jobs) go through here.
Tests patch `perform_upload` rather than reaching into the uploader package,
which means no real network / subprocess fires in the test suite.
"""
from __future__ import annotations

from typing import Any

from tiktok_uploader import tiktok as _tiktok


def perform_upload(
    username: str,
    video_path: str,
    title: str,
    *,
    allow_comment: int = 1,
    allow_duet: int = 0,
    allow_stitch: int = 0,
    visibility_type: int = 0,
    brand_organic_type: int = 0,
    branded_content_type: int = 0,
    ai_label: int = 0,
    proxy: str = "",
) -> bool:
    """Delegate to the underlying uploader. Returns True on success.

    Note: schedule_time is deliberately 0 here — the new scheduler handles
    time-based dispatch on our side, replacing TikTok's unreliable
    server-side schedule_time.
    """
    result = _tiktok.upload_video(
        username,
        video_path,
        title,
        0,  # schedule_time — unused, handled by our scheduler
        allow_comment,
        allow_duet,
        allow_stitch,
        visibility_type,
        brand_organic_type,
        branded_content_type,
        ai_label,
        proxy,
    )
    # upload_video returns True on success, False on most failure paths.
    return bool(result)


def upload_from_options(username: str, video_path: str, title: str, options: dict[str, Any]) -> bool:
    """Convenience: fan out an options dict into perform_upload kwargs."""
    return perform_upload(
        username,
        video_path,
        title,
        allow_comment=int(options.get("allow_comment", 1)),
        allow_duet=int(options.get("allow_duet", 0)),
        allow_stitch=int(options.get("allow_stitch", 0)),
        visibility_type=int(options.get("visibility_type", 0)),
        brand_organic_type=int(options.get("brand_organic_type", 0)),
        branded_content_type=int(options.get("branded_content_type", 0)),
        ai_label=int(options.get("ai_label", 0)),
        proxy=str(options.get("proxy", "")),
    )
