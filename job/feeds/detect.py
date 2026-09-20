"""Feed type and id detection from a careers page link.

The real logic lives in `api/src/companies.py` (`detect_feed`), which the
Worker already bundles and uses when Henry adds a company on the dashboard.
This module puts it at the path the plan calls for (`job/feeds/detect.py`)
and reuses it here too, so a link is recognized the same way everywhere --
one implementation, not two to keep in sync. `job/config.py` adds
`api/src` to `sys.path` before this import runs.
"""
from __future__ import annotations

import job.config  # noqa: F401 - import for its sys.path side effect

from companies import detect_feed  # noqa: E402,F401
