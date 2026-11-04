"""hasc.reader package."""

__all__ = [
    "photo",
    "reader",
    "reading",
    "upright",
    "CloudReader",
    "InvalidReply",
    "ReaderError",
    "TransportError",
    "openrouter_transport",
    "read_sheet",
]


def __getattr__(name):
    if name == "upright":
        from hasc.reader.photo import upright
        return upright
    if name in ("CloudReader", "InvalidReply", "ReaderError", "TransportError", "openrouter_transport"):
        import hasc.reader.reader as _r
        return getattr(_r, name)
    if name == "read_sheet":
        from hasc.reader.reading import read_sheet
        return read_sheet
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
