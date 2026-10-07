from __future__ import annotations

from dataclasses import asdict

from .models import (
    CONTRIBUTION_APPROVED,
    CONTRIBUTION_REJECTED,
    ContributionDecision,
    ContributionSettings,
    LEVEL_COLLABORATOR,
    LEVEL_CONTRIBUTOR,
    LEVEL_NEW,
    LEVEL_TRUSTED,
)
from .repository import ContributionRepository


class ContributionService:
    """Business rules for contribution economy and moderation."""

    def __init__(self, repository: ContributionRepository) -> None:
        self.repo = repository

    async def initialize(self, bot_id: int) -> None:
        defaults = asdict(ContributionSettings())
        existing = await self.repo.get_settings(bot_id)
        if not existing:
            await self.repo.upsert_settings(bot_id, defaults)

    async def settings(self, bot_id: int) -> ContributionSettings:
        await self.initialize(bot_id)
        data = await self.repo.get_settings(bot_id) or {}
        base = asdict(ContributionSettings())
        base.update({k: data[k] for k in base if k in data})
        return ContributionSettings(**base)

    async def profile(self, bot_id: int, user_id: int) -> dict:
        cfg = await self.settings(bot_id)
        return await self.repo.ensure_profile(bot_id, user_id, cfg.initial_credits)

    @staticmethod
    def level_for(reputation: int, approved_contributions: int) -> str:
        if approved_contributions >= 50 and reputation >= 500:
            return LEVEL_TRUSTED
        if approved_contributions >= 20 and reputation >= 200:
            return LEVEL_CONTRIBUTOR
        if approved_contributions >= 5 and reputation >= 40:
            return LEVEL_COLLABORATOR
        return LEVEL_NEW

    @staticmethod
    def ratio(profile: dict) -> float:
        contributions = int(profile.get("approved_contributions") or 0)
        downloads = int(profile.get("downloads") or 0)
        if downloads <= 0:
            return float(contributions or 0)
        return round(contributions / downloads, 2)

    def decorate_profile(self, profile: dict) -> dict:
        item = dict(profile)
        item["ratio"] = self.ratio(item)
        item["level"] = self.level_for(int(item.get("reputation", 0)), int(item.get("approved_contributions", 0)))
        item["can_download"] = int(item.get("credits", 0)) > 0
        return item

    async def authorize_download(self, bot_id: int, user_id: int, cost: int | None = None) -> ContributionDecision:
        profile = await self.profile(bot_id, user_id)
        sanction = await self.repo.active_sanction(bot_id, user_id)
        if sanction:
            return ContributionDecision(False, f"Cuenta restringida: {sanction.get('reason') or 'sanción activa'}", int(profile.get("credits", 0)), int(profile.get("reputation", 0)), self.ratio(profile))
        cfg = await self.settings(bot_id)
        actual_cost = cfg.download_cost if cost is None else max(0, int(cost))
        if int(profile.get("credits", 0)) < actual_cost:
            ratio = self.ratio(profile)
            if int(profile.get("downloads", 0)) >= cfg.new_user_downloads and ratio < cfg.minimum_ratio:
                return ContributionDecision(False, "Necesitas realizar más aportes válidos antes de seguir descargando.", int(profile.get("credits", 0)), int(profile.get("reputation", 0)), ratio)
            return ContributionDecision(False, f"Necesitas {actual_cost} créditos para esta descarga.", int(profile.get("credits", 0)), int(profile.get("reputation", 0)), ratio)
        updated = await self.repo.consume_credits(bot_id, user_id, actual_cost)
        if not updated:
            return ContributionDecision(False, "El saldo cambió antes de completar la operación. Inténtalo de nuevo.", int(profile.get("credits", 0)), int(profile.get("reputation", 0)), self.ratio(profile))
        return ContributionDecision(True, "Descarga autorizada.", int(updated.get("credits", 0)), int(updated.get("reputation", 0)), self.ratio(updated))

    async def register_contribution(self, *, bot_id: int, uploader_id: int, room_id: str, media_type: str, file_id: str, fingerprint: str | None, caption: str | None, event_key: str | None = None) -> dict:
        await self.profile(bot_id, uploader_id)
        duplicate = await self.repo.find_duplicate(bot_id, fingerprint)
        if duplicate:
            return {"status": "DUPLICATE", "item": duplicate, "duplicate_of": duplicate.get("contribution_id")}
        item = await self.repo.create_content(bot_id=bot_id, uploader_id=uploader_id, room_id=room_id, media_type=media_type, file_id=file_id, fingerprint=fingerprint, caption=caption, event_key=event_key)
        await self.repo.increment_contribution_counter(bot_id, uploader_id)
        return {"status": "PENDING", "item": item}

    async def approve_contribution(self, bot_id: int, contribution_id: str, moderator_id: int) -> dict | None:
        cfg = await self.settings(bot_id)
        item = await self.repo.set_content_status(bot_id, contribution_id, CONTRIBUTION_APPROVED, moderator_id, cfg.approved_contribution_credits, cfg.approved_contribution_reputation)
        if not item:
            return None
        updated = await self.repo.grant_contribution_reward(bot_id, int(item["uploader_id"]), cfg.approved_contribution_credits, cfg.approved_contribution_reputation)
        await self.repo.log_action(bot_id=bot_id, moderator_id=moderator_id, action="CONTRIBUTION_APPROVED", target_id=contribution_id, details={"uploader_id": item["uploader_id"], "credits": cfg.approved_contribution_credits})
        item["profile"] = self.decorate_profile(updated or {})
        return item

    async def reject_contribution(self, bot_id: int, contribution_id: str, moderator_id: int, duplicate_of: str | None = None, reason: str | None = None) -> dict | None:
        cfg = await self.settings(bot_id)
        item = await self.repo.set_content_status(bot_id, contribution_id, CONTRIBUTION_REJECTED, moderator_id, 0, -cfg.rejected_reputation_penalty, duplicate_of=duplicate_of)
        if not item:
            return None
        updated = await self.repo.apply_rejection_penalty(bot_id, int(item["uploader_id"]), cfg.rejected_reputation_penalty)
        await self.repo.log_action(bot_id=bot_id, moderator_id=moderator_id, action="CONTRIBUTION_REJECTED", target_id=contribution_id, details={"reason": reason, "duplicate_of": duplicate_of})
        item["profile"] = self.decorate_profile(updated or {})
        return item

    async def report(self, bot_id: int, reporter_id: int, contribution_id: str, reason: str, note: str | None = None) -> dict:
        await self.profile(bot_id, reporter_id)
        report = await self.repo.create_report(bot_id=bot_id, reporter_id=reporter_id, contribution_id=contribution_id, reason=reason, note=note)
        await self.repo.add_report_counter(bot_id, reporter_id)
        await self.repo.log_action(bot_id=bot_id, moderator_id=reporter_id, action="REPORT_CREATED", target_id=contribution_id, details={"report_id": report["report_id"], "reason": reason})
        return report

    async def sanction(self, bot_id: int, user_id: int, moderator_id: int, kind: str, reason: str, minutes: int | None = None) -> dict:
        sanction = await self.repo.create_sanction(bot_id=bot_id, user_id=user_id, moderator_id=moderator_id, kind=kind, reason=reason, minutes=minutes)
        await self.repo.log_action(bot_id=bot_id, moderator_id=moderator_id, action="USER_SANCTIONED", target_id=str(user_id), details={"kind": kind, "reason": reason, "minutes": minutes})
        return sanction

    async def leaderboard(self, bot_id: int, limit: int = 10) -> list[dict]:
        rows = await self.repo.leaderboard(bot_id, limit)
        return [self.decorate_profile(row) for row in rows]

    async def moderation_snapshot(self, bot_id: int, limit: int = 30) -> dict:
        return {"pending": await self.repo.list_pending_content(bot_id, limit), "reports": await self.repo.list_open_reports(bot_id, limit), "stats": await self.repo.stats(bot_id)}
