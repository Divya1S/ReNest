"""Test runner that keeps a test run away from the developer's own files and keys."""

from __future__ import annotations

import os
import shutil
import tempfile
from typing import Any

from django.test.runner import DiscoverRunner
from django.test.utils import override_settings

from .env import AI_MODEL_DEFAULTS, AI_PROVIDER_KEY_VARS

# Everything that decides which AI provider is active (see env.ai_provider).
_AI_ENV_VARS = ("AI_PROVIDER", *(name for names in AI_PROVIDER_KEY_VARS.values() for name in names))


class TempMediaTestRunner(DiscoverRunner):
    """Isolate the whole test run from two things on a developer's machine.

    Uploaded files: tests upload listing photos and room scans through the
    real storage backend. With the default runner those files landed in
    backend/media, next to a developer's own uploads, and were never removed.
    MEDIA_ROOT points at a throwaway directory instead.

    AI provider keys: a real key in backend/.env would let a test reach a
    live provider (slow, flaky, and it spends the developer's credits). The
    key variables are blanked for the run and the model names are pinned, so
    every test starts with AI switched off and the tests that exercise it
    set a fake key and a fake client themselves.
    """

    def setup_test_environment(self, **kwargs: Any) -> None:
        super().setup_test_environment(**kwargs)
        self._media_root = tempfile.mkdtemp(prefix="renest-test-media-")
        self._saved_ai_env = {name: os.environ.get(name) for name in _AI_ENV_VARS}
        for name in _AI_ENV_VARS:
            os.environ[name] = ""
        self._settings_override = override_settings(MEDIA_ROOT=self._media_root, **AI_MODEL_DEFAULTS["gemini"])
        self._settings_override.enable()

    def teardown_test_environment(self, **kwargs: Any) -> None:
        self._settings_override.disable()
        for name, value in self._saved_ai_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        shutil.rmtree(self._media_root, ignore_errors=True)
        super().teardown_test_environment(**kwargs)
