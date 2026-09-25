"""Shared HTTP plumbing: the app, errors, the owner cookie, workers, limits.

No feature logic lives here. Needs the `api` extra; nothing outside `api/` and
the features' `api.py` may import this, so the CLI runs without it.
"""
