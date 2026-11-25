"""Settings loaded from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    poll_minutes: int
    max_height: int
    retention_days: int
    watched_grace_hours: int
    max_concurrent_downloads: int
    cookies_file: str | None

    @property
    def db_path(self) -> Path:
        return self.data_dir / "feed.db"

    @property
    def videos_dir(self) -> Path:
        return self.data_dir / "videos"

    @property
    def thumbs_dir(self) -> Path:
        return self.data_dir / "thumbs"


def load_settings(env: Mapping[str, str] = os.environ) -> Settings:
    def num(name: str, default: int) -> int:
        return int(env.get(name, default))

    return Settings(
        data_dir=Path(env.get("DATA_DIR", "/data")),
        poll_minutes=num("POLL_MINUTES", 30),
        max_height=num("MAX_HEIGHT", 720),
        retention_days=num("RETENTION_DAYS", 14),
        watched_grace_hours=num("WATCHED_GRACE_HOURS", 24),
        max_concurrent_downloads=max(1, num("MAX_CONCURRENT_DOWNLOADS", 1)),
        cookies_file=env.get("YTDLP_COOKIES_FILE") or None,
    )
