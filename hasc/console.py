"""Keep printing from raising on a console that cannot show Vietnamese."""
import sys


def make_output_safe(streams=None):
    """Make stdout and stderr replace what their encoding cannot hold with backslash escapes.

    On the office Windows PC a console or redirected file may use cp1252, and printing a Vietnamese
    message to it raises UnicodeEncodeError, which would crash a service exactly when it reports an
    error. The services run with PYTHONUTF8=1 (the bundle sets it); this keeps a plain run safe too.
    The encoding is left alone so a real console keeps working; only the error handling changes.
    """
    for stream in streams or (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(errors="backslashreplace")
            except (ValueError, OSError):  # a closed or detached stream: nothing to protect
                pass
