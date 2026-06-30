"""Platform DB must not re-run schema/repair on every SSO request."""
from __future__ import annotations

import os
import tempfile
from unittest.mock import patch

from nexal_platform.platform_db import PlatformDatabase, reset_platform_schema_cache_for_tests


def test_platform_schema_init_runs_once_per_data_root(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        data_root = os.path.join(tmp, "runtime-data")
        monkeypatch.setenv("NEXAL_DATA_DIR", data_root)
        reset_platform_schema_cache_for_tests()

        with patch(
            "nexal_platform.config.repair_all_stale_workspace_paths",
            side_effect=lambda _platform: 1,
        ) as repair_mock:
            PlatformDatabase()
            PlatformDatabase()
            PlatformDatabase()
            assert repair_mock.call_count == 1

            reset_platform_schema_cache_for_tests()
            monkeypatch.setenv("NEXAL_DATA_DIR", os.path.join(tmp, "other-root"))
            PlatformDatabase()
            assert repair_mock.call_count == 2
