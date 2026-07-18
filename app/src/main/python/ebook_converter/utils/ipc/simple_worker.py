"""In-process replacement for Calibre's subprocess simple worker on Android."""

import importlib
import traceback


class WorkerError(Exception):
    """Compatibility error raised when an in-process worker target fails."""

    def __init__(self, message, orig_tb="", log_path=None):
        super().__init__(message)
        self.orig_tb = orig_tb
        self.log_path = log_path
        if orig_tb:
            self.add_note(f"Original traceback:\n{orig_tb}")
        if log_path:
            self.add_note(f"Log path: {log_path}")


def fork_job(
    module_name,
    function_name,
    args=(),
    kwargs=None,
    no_output=False,
    **_options,
):
    """Run a worker target synchronously and return Calibre's result shape.

    Android applications cannot use Calibre's desktop subprocess worker model.
    Executing in the packaged Python process preserves the public API without
    relying on ``fork`` or launching another interpreter.
    """
    try:
        module = importlib.import_module(module_name)
        function = getattr(module, function_name)
        result = function(*(args or ()), **(kwargs or {}))
    except Exception as error:
        raise WorkerError("Worker failed", traceback.format_exc()) from error

    answer = {"result": result}
    if not no_output:
        answer["stdout_stderr"] = None
    return answer
