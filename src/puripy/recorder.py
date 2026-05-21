import sys
import os
import time
import copy
import zlib
import pickle
import base64
import types as _types
from typing import Any, Optional

import msgpack

_BORING_TYPES = (
    _types.FunctionType, _types.MethodType, _types.BuiltinFunctionType,
    _types.BuiltinMethodType, _types.ModuleType, type,
)


def _serialize_value(val: Any) -> Any:
    """Convert a Python value to a msgpack-serializable form."""
    if val is None or isinstance(val, (bool, int, float, str)):
        return val
    if isinstance(val, bytes):
        return {"__bytes__": base64.b64encode(val).decode("ascii")}
    if isinstance(val, set):
        return {"__set__": [_serialize_value(v) for v in val]}
    if isinstance(val, (list, tuple)):
        return [_serialize_value(v) for v in val]
    if isinstance(val, dict):
        return {str(k): _serialize_value(v) for k, v in val.items()}
    # Store functions/modules/types as repr — pickling them fails at replay time
    # because the script isn't importable via its original module path.
    if isinstance(val, _BORING_TYPES):
        try:
            return {"__repr__": repr(val)[:200]}
        except Exception:
            return {"__repr__": f"<{type(val).__name__}: unserializable>"}
    try:
        return {"__pickle__": base64.b64encode(pickle.dumps(val)).decode("ascii")}
    except Exception:
        pass
    try:
        return {"__repr__": repr(val)[:200]}
    except Exception:
        return {"__repr__": f"<{type(val).__name__}: unserializable>"}


def _snapshot_locals(f_locals: dict) -> dict:
    """Take a serializable snapshot of local variables, ignoring dunder names."""
    snapshot = {}
    for name, val in f_locals.items():
        if name.startswith("__"):
            continue
        try:
            snapshot[name] = _serialize_value(copy.deepcopy(val))
        except Exception:
            snapshot[name] = {"__repr__": f"<deepcopy failed: {type(val).__name__}>"}
    return snapshot


class Recorder:
    """
    Records Python execution into a compressed trace file.

    Usage:
        recorder = Recorder("out.trace", target_file="script.py")
        recorder.start()
        # ... run user code ...
        recorder.stop()

    Or as a context manager:
        with Recorder("out.trace", target_file="script.py"):
            runpy.run_path("script.py", run_name="__main__")
    """

    def __init__(self, output_path: str, target_file: Optional[str] = None):
        self.output_path = output_path
        self.target_file = os.path.abspath(target_file) if target_file else None
        self.frames: list = []
        self._start_time: float = 0.0
        self._scope_stack: list = []

    def _is_user_code(self, filename: str) -> bool:
        if not filename or filename.startswith("<"):
            return False
        if "lib/python" in filename or "site-packages" in filename:
            return False
        if self.target_file:
            return os.path.abspath(filename) == self.target_file
        return True

    def _trace_fn(self, frame, event, arg):
        if not self._is_user_code(frame.f_code.co_filename):
            return None

        t = round(time.monotonic() - self._start_time, 6)
        base = {
            "filename": frame.f_code.co_filename,
            "lineno": frame.f_lineno,
            "func": frame.f_code.co_name,
            "t": t,
        }

        if event == "call":
            snap = _snapshot_locals(dict(frame.f_locals))
            self._scope_stack.append(dict(snap))
            self.frames.append({**base, "event": "call", "locals": snap})

        elif event == "line":
            snap = _snapshot_locals(dict(frame.f_locals))
            if self._scope_stack:
                prev = self._scope_stack[-1]
                delta = {k: v for k, v in snap.items() if k not in prev or prev[k] != v}
                self._scope_stack[-1] = snap
                self.frames.append({**base, "event": "line", "locals": delta, "delta": True})
            else:
                self.frames.append({**base, "event": "line", "locals": snap})

        elif event == "return":
            snap = _snapshot_locals(dict(frame.f_locals))
            self.frames.append({**base, "event": "return", "locals": snap, "retval": _serialize_value(arg)})
            if self._scope_stack:
                self._scope_stack.pop()

        elif event == "exception":
            exc_type, exc_value, _ = arg
            snap = _snapshot_locals(dict(frame.f_locals))
            if self._scope_stack:
                self._scope_stack[-1] = snap
            self.frames.append({
                **base,
                "event": "exception",
                "locals": snap,
                "exc": {"type": exc_type.__name__, "value": str(exc_value)},
            })

        return self._trace_fn

    def start(self) -> None:
        self.frames = []
        self._scope_stack = []
        self._start_time = time.monotonic()
        sys.settrace(self._trace_fn)

    def stop(self) -> None:
        sys.settrace(None)
        self._write()

    def _write(self) -> None:
        payload = msgpack.packb(
            {
                "version": 2,
                "target": self.target_file or "",
                "frame_count": len(self.frames),
                "frames": self.frames,
            },
            use_bin_type=True,
        )
        with open(self.output_path, "wb") as f:
            f.write(zlib.compress(payload, level=6))

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *_):
        self.stop()
