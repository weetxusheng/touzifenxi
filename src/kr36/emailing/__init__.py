"""Email rendering and sending utilities for kr36 briefs."""

from .preview_assets import save_brief_preview_assets
from .send import EmailSendResult, send_latest_kr36_brief_email, shanghai_yesterday
from .template import render_kr36_brief_email

__all__ = [
    "EmailSendResult",
    "render_kr36_brief_email",
    "save_brief_preview_assets",
    "send_latest_kr36_brief_email",
    "shanghai_yesterday",
]

