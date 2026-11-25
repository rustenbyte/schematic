class SchematicError(Exception):
    """An error the command line reports to the user as it is, without a traceback."""


class UnknownNameError(SchematicError):
    pass


class AmbiguousNameError(SchematicError):
    pass


class ProbeFailedError(SchematicError):
    pass


class MissingResultError(SchematicError):
    pass


class TargetError(SchematicError):
    pass
