from __future__ import annotations

import os

# Isolate tests from a developer's `.env` (real credentials, real database).
# Must run before `core.settings` is imported.
os.environ["CHAINSIGHT_ENV_FILE"] = ""
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("LOG_FORMAT", "console")
os.environ.setdefault("LOG_LEVEL", "WARNING")
# The suite makes thousands of requests from one client; rate-limit tests enable it explicitly.
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")
