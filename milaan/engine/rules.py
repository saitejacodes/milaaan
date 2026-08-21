"""Named deterministic thresholds and their rationale."""

from __future__ import annotations


SUFFIX_MIN_LENGTH = 6  # Shorter fragments collide too easily in bank narrations.
CONFUSABLE_PAIRS = frozenset({
    frozenset(("0", "o")), frozenset(("1", "i")), frozenset(("1", "l")),
})

A0_CONFIDENCE = 1.0
A1_CONFIDENCE = 0.9
B0_CONFIDENCE = 1.0
B2_FULL_CONFIDENCE = 0.9
B2_SUFFIX_CONFIDENCE = 0.8
B2_CORRUPTED_CONFIDENCE = 0.75
