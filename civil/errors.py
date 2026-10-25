class CivilInputError(ValueError):
    """The caller's input cannot be used (bad file, impossible geometry).
    The API turns this into HTTP 422 with the message."""
