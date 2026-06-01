"""Build the per-article content cleaner."""

from __future__ import annotations

from collections.abc import Callable

from .config import AppConfig
from .content_scrubber import scrub_extracted_noise
from .models import Article


def make_content_cleaner(config: AppConfig) -> Callable[[Article, str], str]:
    def scrub_only(_article: Article, text: str) -> str:
        return scrub_extracted_noise(text)

    return scrub_only
