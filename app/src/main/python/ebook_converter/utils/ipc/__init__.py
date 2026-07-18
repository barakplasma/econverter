"""Android-compatible IPC helpers.

The desktop Calibre code uses subprocess workers. Android/Chaquopy cannot fork
those workers, so eConverter supplies an in-process implementation where needed.
"""
