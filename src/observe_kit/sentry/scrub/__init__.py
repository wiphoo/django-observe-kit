"""Sentry PII-scrub internals, decomposed into single-responsibility seams.

The scrubber runs in Sentry's ``before_send`` hook and must strip PII from every
field of an event. It historically lived as one ~2,300-line module that grew a
parallel, hand-rolled implementation for each concern (decode, email, URL,
traversal). This package splits those into composable seams so each concern has
exactly one owner:

- :mod:`.decode` — the single bounded percent-decode seam.

Public orchestration (``scrub_event``, ``init_sentry``) stays in
``observe_kit.sentry.config``, which imports from here.
"""
