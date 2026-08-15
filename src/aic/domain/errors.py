class ContractValidationError(ValueError):
    """Raised when a value violates the activated B1.2 machine contract."""


class CanonicalSerializationError(ValueError):
    """Raised when a value cannot be serialized by B1_CANONICAL_SERIALIZER_V1."""
