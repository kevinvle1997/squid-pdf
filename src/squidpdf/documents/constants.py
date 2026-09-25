"""How long a document lives, and how often the dead ones are cleared."""

from __future__ import annotations

IDLE_S = 3600  # a document untouched this long is deleted; page images cache as long
SWEEP_EVERY_S = 60
