"""Core source adapter entry for kr36."""

from kr36.source_adapter import *  # noqa: F401,F403
from kr36.core.source.common import _safe_filename, _safe_path_component, kr36_debug_log_file

# Explicit re-exports for internal helper symbols (star-import does not include
# underscore-prefixed names, but topic/fulltext_index relies on them).

