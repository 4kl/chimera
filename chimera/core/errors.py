class ChimeraError(Exception):
    """Base class for all Chimera errors."""


class PerceptionError(ChimeraError):
    pass


class ExecutionError(ChimeraError):
    pass


class HealError(ChimeraError):
    pass


class ReasoningError(ChimeraError):
    pass
