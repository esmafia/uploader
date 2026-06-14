"""Browse videos already on the shared volume (VideosDirPath/)."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter

from api.schemas import VideoFileInfo
from tiktok_uploader.Config import Config

router = APIRouter(prefix="/api/videos", tags=["videos"])


def _videos_dir() -> str:
    return os.path.join(os.getcwd(), Config.get().videos_dir)


@router.get("", response_model=List[VideoFileInfo])
def list_videos():
    d = _videos_dir()
    if not os.path.isdir(d):
        return []
    out: list[VideoFileInfo] = []
    for name in sorted(os.listdir(d)):
        full = os.path.join(d, name)
        if not os.path.isfile(full):
            continue
        st = os.stat(full)
        out.append(
            VideoFileInfo(
                name=name,
                size_bytes=st.st_size,
                modified_at=datetime.fromtimestamp(st.st_mtime, tz=timezone.utc),
            )
        )
    return out
