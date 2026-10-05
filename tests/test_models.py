from app.core.models import BotConfig

def test_bot_config_ignores_legacy_unknown_fields():
    cfg = BotConfig.from_mapping({"platform_name": "Mansia", "language": "es", "max_members_default": 250})
    assert cfg.language == "es"
    assert cfg.max_members_default == 250
