"""Contribution + moderation addon for child bots.

This addon is intentionally isolated from the existing platform files.
Wire it into the current child runtime using the integration notes.
"""

from .repository import ContributionRepository
from .service import ContributionService

__all__ = ["ContributionRepository", "ContributionService"]
