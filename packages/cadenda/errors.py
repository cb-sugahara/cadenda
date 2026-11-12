class CadendaError(Exception):
    """A stable error code and safe, human-readable description."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)
