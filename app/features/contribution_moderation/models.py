from __future__ import annotations

from dataclasses import dataclass


CONTRIBUTION_PENDING = "PENDING"
CONTRIBUTION_APPROVED = "APPROVED"
CONTRIBUTION_REJECTED = "REJECTED"

REPORT_OPEN = "OPEN"
REPORT_RESOLVED = "RESOLVED"
REPORT_DISMISSED = "DISMISSED"

SANCTION_ACTIVE = "ACTIVE"
SANCTION_EXPIRED = "EXPIRED"
SANCTION_REVOKED = "REVOKED"

LEVEL_NEW = "Nuevo"
LEVEL_COLLABORATOR = "Colaborador"
LEVEL_CONTRIBUTOR = "Contribuidor"
LEVEL_TRUSTED = "Confiable"


@dataclass(frozen=True, slots=True)
class ContributionSettings:
    initial_credits: int = 15
    approved_contribution_credits: int = 10
    approved_contribution_reputation: int = 10
    download_cost: int = 5
    rejected_reputation_penalty: int = 2
    duplicate_reputation_penalty: int = 1
    report_reputation_bonus: int = 1
    minimum_ratio: float = 0.25
    new_user_downloads: int = 3


@dataclass(frozen=True, slots=True)
class ContributionDecision:
    allowed: bool
    reason: str
    balance: int
    reputation: int
    ratio: float
