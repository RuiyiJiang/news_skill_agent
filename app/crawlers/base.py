from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from app.models import SourceConfig, NewsItem


class BaseNewsParser(ABC):
    @abstractmethod
    def fetch_recent(self, source: SourceConfig, now: datetime) -> list[NewsItem]:
        """Fetch recent news items for a source."""
