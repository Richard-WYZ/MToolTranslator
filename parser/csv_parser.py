"""Compatibility errors for the removed CSV translation format."""


def _unsupported_csv(*args, **kwargs):
    raise ValueError("CSV translation is no longer supported; use MTool JSON")


parse_csv = _unsupported_csv
serialize_csv = _unsupported_csv
get_column_mapping = _unsupported_csv


__all__ = ["get_column_mapping", "parse_csv", "serialize_csv"]
