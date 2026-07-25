"""Compatibility facade for JSON helpers moved into the translation domain."""

from translation.input.json_io import load_json_items as parse_json
from translation.output.json_io import serialize_json_items


def serialize_json(data, file_path=None):
    result = serialize_json_items(data)
    if file_path:
        with open(file_path, "w", encoding="utf-8") as stream:
            stream.write(result)
        return None
    return result


__all__ = ["parse_json", "serialize_json"]
