"""Test runner that keeps uploaded files out of the developer's media folder."""

from __future__ import annotations

import shutil
import tempfile
from typing import Any

from django.test.runner import DiscoverRunner
from django.test.utils import override_settings


class TempMediaTestRunner(DiscoverRunner):
    """Point MEDIA_ROOT at a throwaway directory for the whole test run.

    Tests upload listing photos and room scans through the real storage
    backend. With the default runner those files landed in backend/media,
    next to a developer's own uploads, and were never removed.
    """

    def setup_test_environment(self, **kwargs: Any) -> None:
        super().setup_test_environment(**kwargs)
        self._media_root = tempfile.mkdtemp(prefix="renest-test-media-")
        self._media_override = override_settings(MEDIA_ROOT=self._media_root)
        self._media_override.enable()

    def teardown_test_environment(self, **kwargs: Any) -> None:
        self._media_override.disable()
        shutil.rmtree(self._media_root, ignore_errors=True)
        super().teardown_test_environment(**kwargs)
