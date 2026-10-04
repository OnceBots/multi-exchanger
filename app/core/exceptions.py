class PlatformError(Exception):
    """Base application error."""


class BotStartupError(PlatformError):
    pass


class BotShutdownError(PlatformError):
    pass


class InvalidBotTokenError(PlatformError):
    pass


class BotAlreadyRunningError(PlatformError):
    pass


class BotNotFoundError(PlatformError):
    pass


class TenantIsolationError(PlatformError):
    pass


class PermissionDeniedError(PlatformError):
    pass
