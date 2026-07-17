"""Logical occurrence polarity used by the ELK-compatible compiler.

This is a compact internal mask rather than a public OWL visitor API.  Complement switches
positive and negative occurrences; dual occurrences remain dual.
"""

from __future__ import annotations

from enum import IntFlag


class IndexPolarity(IntFlag):
    """Occurrence directions carried by one compiler conversion."""

    NEUTRAL = 0
    NEGATIVE = 1
    POSITIVE = 2
    DUAL = NEGATIVE | POSITIVE

    @property
    def negative(self) -> int:
        """Return one when this mask contains a negative occurrence."""

        return int(bool(self & IndexPolarity.NEGATIVE))

    @property
    def positive(self) -> int:
        """Return one when this mask contains a positive occurrence."""

        return int(bool(self & IndexPolarity.POSITIVE))

    def complementary(self) -> IndexPolarity:
        """Return the polarity used below an object complement."""

        if self is IndexPolarity.NEGATIVE:
            return IndexPolarity.POSITIVE
        if self is IndexPolarity.POSITIVE:
            return IndexPolarity.NEGATIVE
        return self


__all__ = ["IndexPolarity"]
