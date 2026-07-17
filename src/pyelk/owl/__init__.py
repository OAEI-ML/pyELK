"""Exact re-exports of pyowl-core's immutable OWL 2 structural model.

No class, wrapper, ELK compatibility key, or unsupported-node placeholder is defined here.
Pinned-ELK adaptations belong exclusively to pyELK's future private compiler.
"""

from __future__ import annotations

from pyowl_core import model as _model
from pyowl_core.model import *  # noqa: F403

__all__ = list(_model.__all__)
