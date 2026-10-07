from app.features.contribution_moderation.models import ContributionSettings
from app.features.contribution_moderation.service import ContributionService


def test_level_progression():
    assert ContributionService.level_for(0, 0) == "Nuevo"
    assert ContributionService.level_for(40, 5) == "Colaborador"
    assert ContributionService.level_for(200, 20) == "Contribuidor"
    assert ContributionService.level_for(500, 50) == "Confiable"


def test_ratio_zero_downloads():
    assert ContributionService.ratio({"approved_contributions": 4, "downloads": 0}) == 4.0
    assert ContributionService.ratio({"approved_contributions": 4, "downloads": 8}) == 0.5


def test_default_settings():
    cfg = ContributionSettings()
    assert cfg.initial_credits == 15
    assert cfg.approved_contribution_credits == 10
    assert cfg.download_cost == 5
