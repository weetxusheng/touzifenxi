"""Disable outbound translation during tests unless overridden."""

import os

os.environ.setdefault("FEEDCORE_SKIP_TRANSLATE", "1")
