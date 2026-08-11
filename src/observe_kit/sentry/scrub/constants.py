"""Shared literals for the Sentry PII-scrub seams.

Kept in a dependency-free module so both the orchestration in
``observe_kit.sentry.config`` and the individual scrub seams (``emails``, …)
can import them without forming an import cycle.
"""

from __future__ import annotations

import re

# The marker written in place of a value redacted wholesale. Matches Sentry's
# own ``EventScrubber`` output so redactions look native in the Sentry UI.
REDACTED = "[Filtered]"

# Splits a value into whitespace-delimited tokens while keeping the separators
# (capturing group), so newlines/tabs aren't collapsed when re-joining. Used on
# the ``before_send`` hot path, so it is compiled once here.
WS_SPLIT_RE = re.compile(r"(\s+)")
