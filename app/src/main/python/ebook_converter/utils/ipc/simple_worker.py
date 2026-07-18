"""In-process replacement for Calibre's subprocess simple worker on Android."""

import importlib


def fork_job(module_name, function_name, args=(), kwargs=None, no_output=False, **_options):
    """Run a worker target synchronously and return Calibre's result shape.

    Android applications cannot use Calibre's desktop subprocess worker model.
    The eConverter call sites only need the target result, so executing in the
    packaged Python process preserves their API without relying on ``fork``.
    """
    del no_output  # Kept for call-site compatibility.
    module = importlib.import_module(module_name)
    function = getattr(module, function_name)
    result = function(*(args or ()), **(kwargs or {}))
    return {"result": result, "stdout": "", "stderr": ""}
