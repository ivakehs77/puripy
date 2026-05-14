import sys
import os
import time
import copy
import zlib
import pickle
import base64
from typing import Any, Optional

import msgpack

_MISSING = object()  # sentinel for delta comparison — never equal to any real value


def _serialize_value(val: Any) -> Any:
    """
    Convert a Python value to a msgpack-serializable form.

    Strategy (in order):
      1. Primitives pass through directly.
      2. Containers are recursed into.
      3. Custom objects: try pickle (so the replayer can reconstruct them later).
      4. Fall back to repr() for unpicklable objects (file handles, locks, etc.).
    """
    if val is None or isinstance(val, (bool, int, float, str)):
        return val
    if isinstance(val, bytes):
        return {"__bytes__": base64.b64encode(val).decode()}
    if isinstance(val, (set, frozenset)):
        return {"__set__": [_serialize_value(v) for v in sorted(val, key=str)]}
    if isinstance(val, (list, tuple)):
        return [_serialize_value(v) for v in val]
    if isinstance(val, dict):
        return {str(k): _serialize_value(v) for k, v in val.items()}
    # Custom objects: try pickle first so the replayer can reconstruct them.
    # Always store __type__ so the replayer can filter without unpickling.
    type_name = type(val).__name__
    try:
        return {
            "__pickle__": base64.b64encode(pickle.dumps(val, protocol=2)).decode(),
            "__type__": type_name,
        }
    except Exception:
        pass
    # Last resort: repr string.
    try:
        return {"__repr__": f"{type_name}: {repr(val)[:300]}"}
    except Exception:
        return {"__repr__": f"{type_name}: <unserializable>"}


def _snapshot_locals(f_locals: dict) -> dict:
    """Take a serializable snapshot of local variables, ignoring dunder names."""
    snapshot = {}
    for name, val in f_locals.items():
        if name.startswith("__"):
            continue
        try:
            snapshot[name] = _serialize_value(copy.deepcopy(val))
        except Exception:
            snapshot[name] = {"__repr__": f"{type(val).__name__}: <deepcopy failed>"}
    return snapshot


class Recorder:
    """
    Records Python execution into a compressed trace file using delta encoding.

    Each 'call' event stores a full variable snapshot.  Subsequent 'line' events
    within that scope store only the variables that changed — the replayer
    reconstructs full state on load.  This cuts trace size significantly for
    functions with many lines that mutate only a few variables per step.

    Usage:
        with Recorder("out.trace", target_file="script.py"):
            runpy.run_path("script.py", run_name="__main__")
    """

    def __init__(self, output_path: str, target_file: Optional[str] = None):
        self.output_path = output_path
        self.target_file = os.path.abspath(target_file) if target_file else None
        self.frames: list = []
        self._start_time: float = 0.0
        self._call_stack: list = []  # stack of full snapshot dicts, one per active scope

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

        entry = {
            "event": event,
            "filename": frame.f_code.co_filename,
            "lineno": frame.f_lineno,
            "func": frame.f_code.co_name,
            "t": round(time.monotonic() - self._start_time, 6),
        }

        if event == "call":
            # Full snapshot — function arguments are already in f_locals.
            snapshot = _snapshot_locals(dict(frame.f_locals))
            self._call_stack.append(snapshot)
            entry["locals"] = snapshot

        elif event == "line":
            current = _snapshot_locals(dict(frame.f_locals))
            if self._call_stack:
                prev = self._call_stack[-1]
                # Only store variables that are new or changed since last line.
                delta = {k: v for k, v in current.items() if prev.get(k, _MISSING) != v}
                self._call_stack[-1] = current
                entry["locals"] = delta
                entry["delta"] = True
            else:
                entry["locals"] = current  # fallback for unexpected stack underflow

        elif event == "return":
            if self._call_stack:
                self._call_stack.pop()
            entry["locals"] = {}
            entry["retval"] = _serialize_value(arg)

        elif event == "exception":
            exc_type, exc_val, _ = arg
            # Full snapshot so the user sees complete state at the error site.
            snapshot = _snapshot_locals(dict(frame.f_locals))
            if self._call_stack:
                self._call_stack[-1] = snapshot  # resync stack after exception
            entry["locals"] = snapshot
            entry["exc"] = {
                "type": exc_type.__name__ if exc_type else "Unknown",
                "value": str(exc_val),
            }

        self.frames.append(entry)
        return self._trace_fn

    def start(self) -> None:
        self.frames = []
        self._call_stack = []
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
