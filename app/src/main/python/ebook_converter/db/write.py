"""Compatibility subset of Calibre's database write helpers."""

import re


series_index_pat = re.compile(r'(.*)\s+\[([.0-9]+)\]$')


def get_series_values(val):
    """Return a series name and optional numeric index from ``Name [1.5]``."""
    if not val:
        return val, None
    match = series_index_pat.match(val.strip())
    if match is not None:
        idx = match.group(2)
        try:
            return match.group(1).strip(), float(idx)
        except Exception:
            pass
    return val, None
