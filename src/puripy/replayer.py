import zlib
import pickle
import base64
from typing import Any, Optional

import msgpack


def _deserialize_value(val: Any) -> Any:
    """Reverse _serialize_value: reconstruct Python objects from the stored form."""
    if val is None or isinstance(val, (bool, int, float, str)):
        return val
    if isinstance(val, list):
        return [_deserialize_value(v) for v in val]
    if isinstance(val, dict):
        if "__pickle__" in val:
            try:
                return pickle.loads(base64.b64decode(val["__pickle__"]))
            except Exception:
                return "<pickle load failed>"
        if "__bytes__" in val:
            return base64.b64decode(val["__bytes__"])
        if "__set__" in val:
            return set(_deserialize_value(v) for v in val["__set__"])
        if "__repr__" in val:
            return val["__repr__"]
        return {k: _deserialize_value(v) for k, v in val.items()}
    return val


def _format_value(val: Any, max_len: int = 120) -> str:
    """Human-readable string for a deserialized value."""
    s = repr(val) if not isinstance(val, str) else val
    return s if len(s) <= max_len else s[:max_len] + " ..."


class TraceFrame:
    """A single recorded execution event."""

    __slots__ = ("event", "filename", "lineno", "func", "locals", "t", "retval", "exc")

    def __init__(self, raw: dict):
        self.event: str = raw["event"]
        self.filename: str = raw["filename"]
        self.lineno: int = raw["lineno"]
        self.func: str = raw["func"]
        self.locals: dict = raw.get("locals", {})
        self.t: float = raw.get("t", 0.0)
        self.retval: Any = raw.get("retval")
        self.exc: Optional[dict] = raw.get("exc")

    def get_local(self, name: str) -> Any:
        """Return the deserialized value of a local variable, or None if absent."""
        if name not in self.locals:
            return None
        return _deserialize_value(self.locals[name])

    def __repr__(self) -> str:
        return f"<TraceFrame {self.event} {self.func}:{self.lineno}>"


class Replayer:
    """
    Loads a .trace file and provides O(1) frame access for time-travel navigation.

    The distinction between "frames" and "lines":
      - A raw frame is every trace event (call, line, return, exception).
      - A "line step" skips to the next/prev line event only — this is what
        the user sees as a single step in the REPL.
    """

    def __init__(self, frames: list, target: str = ""):
        self.frames = frames
        self.target = target
        self._pos: int = 0
        self._line_index: dict = self._build_line_index()
        self._line_positions: list = [i for i, f in enumerate(frames) if f.event == "line"]
        self._depths: list = self._compute_depths()

    @classmethod
    def load(cls, path: str) -> "Replayer":
        with open(path, "rb") as f:
            data = msgpack.unpackb(zlib.decompress(f.read()), raw=False)
        frames = cls._reconstruct(data["frames"])
        return cls(frames, target=data.get("target", ""))

    @staticmethod
    def _reconstruct(raw_frames: list) -> list:
        """
        Expand delta-encoded frames into full snapshots.

        Frames with "delta": True store only changed variables.  We replay
        them forward to rebuild complete locals at every frame, so the rest
        of the replayer can treat all frames uniformly.

        Version-1 traces lack the "delta" flag and are loaded unchanged.
        """
        frames = []
        scope_stack: list = []  # stack of full snapshot dicts

        for raw in raw_frames:
            event = raw["event"]

            if event == "call":
                scope_stack.append(dict(raw.get("locals", {})))
                # call frames already hold a full snapshot

            elif event == "line" and raw.get("delta"):
                if scope_stack:
                    scope_stack[-1].update(raw.get("locals", {}))
                    raw = {**raw, "locals": dict(scope_stack[-1])}
                # else: no scope (shouldn't happen) — use delta as-is

            elif event in ("return", "exception"):
                # exception frames carry a full snapshot; resync the stack.
                if event == "exception" and scope_stack:
                    scope_stack[-1].update(raw.get("locals", {}))

            frames.append(TraceFrame(raw))

            if event == "return" and scope_stack:
                scope_stack.pop()

        return frames

    def _compute_depths(self) -> list:
        """
        Pre-compute call-stack depth for every frame.
        'call' events increment depth before being stored; 'return' events
        decrement depth after being stored — so each frame records the depth
        it actually ran at.
        """
        depths = []
        depth = 0
        for frame in self.frames:
            if frame.event == "call":
                depth += 1
            depths.append(depth)
            if frame.event == "return":
                depth -= 1
        return depths

    def _build_line_index(self) -> dict:
        """Map source line number -> first frame index of each 'line' event on that line."""
        index: dict = {}
        for i, frame in enumerate(self.frames):
            if frame.event == "line":
                index.setdefault(frame.lineno, []).append(i)
        return index

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    @property
    def pos(self) -> int:
        return self._pos

    @property
    def current(self) -> TraceFrame:
        return self.frames[self._pos]

    @property
    def line_step(self) -> int:
        """Which line-event step we're on (among line events only)."""
        import bisect
        idx = bisect.bisect_right(self._line_positions, self._pos) - 1
        return max(idx, 0)

    @property
    def total_line_steps(self) -> int:
        return len(self._line_positions)

    def step(self, n: int = 1) -> "Replayer":
        """Advance n raw frames."""
        self._pos = min(self._pos + n, len(self.frames) - 1)
        return self

    def back(self, n: int = 1) -> "Replayer":
        """Go back n raw frames."""
        self._pos = max(self._pos - n, 0)
        return self

    def step_line(self, n: int = 1) -> "Replayer":
        """Advance n source-line executions (line events only)."""
        import bisect
        current_idx = bisect.bisect_right(self._line_positions, self._pos) - 1
        target_idx = min(current_idx + n, len(self._line_positions) - 1)
        if self._line_positions:
            self._pos = self._line_positions[target_idx]
        return self

    def back_line(self, n: int = 1) -> "Replayer":
        """Go back n source-line executions (line events only)."""
        import bisect
        current_idx = bisect.bisect_left(self._line_positions, self._pos)
        # If we're on a line event, step back from here; otherwise step back from prev
        if current_idx < len(self._line_positions) and self._line_positions[current_idx] == self._pos:
            target_idx = current_idx - n
        else:
            target_idx = current_idx - n
        target_idx = max(target_idx, 0)
        if self._line_positions:
            self._pos = self._line_positions[target_idx]
        return self

    def goto(self, target: int) -> "Replayer":
        """Jump to a raw frame index."""
        if not 0 <= target < len(self.frames):
            raise IndexError(f"Frame {target} out of range (0-{len(self.frames) - 1})")
        self._pos = target
        return self

    def goto_line(self, lineno: int) -> "Replayer":
        """Jump to the first recorded execution of a source line."""
        if lineno not in self._line_index:
            raise ValueError(f"Line {lineno} was never executed")
        self._pos = self._line_index[lineno][0]
        return self

    @property
    def depth(self) -> int:
        return self._depths[self._pos]

    def step_over(self) -> "Replayer":
        """
        Advance to the next line in the current scope.
        If the current line calls a function, that entire call is skipped.
        """
        current_depth = self._depths[self._pos]
        pos = self._pos + 1
        while pos < len(self.frames):
            if self.frames[pos].event == "line" and self._depths[pos] <= current_depth:
                self._pos = pos
                return self
            pos += 1
        return self.step_line()

    def step_out(self) -> "Replayer":
        """
        Run until the current function returns, then stop at the caller's next line.
        """
        current_depth = self._depths[self._pos]
        if current_depth <= 1:
            return self  # already at top level
        pos = self._pos + 1
        while pos < len(self.frames):
            if self.frames[pos].event == "line" and self._depths[pos] < current_depth:
                self._pos = pos
                return self
            pos += 1
        return self

    # ------------------------------------------------------------------
    # Inspection
    # ------------------------------------------------------------------

    def show(self, name: str) -> Any:
        """Return deserialized value of a local variable at the current frame."""
        return self.current.get_local(name)

    def locals_at(self, pos: Optional[int] = None) -> dict:
        """Return all deserialized locals at pos (default: current)."""
        frame = self.frames[pos] if pos is not None else self.current
        return {k: _deserialize_value(v) for k, v in frame.locals.items()}

    def diff(self, pos_a: int, pos_b: int) -> dict:
        """
        Compare locals between two frame positions.
        Returns a dict of {name: (value_at_a, value_at_b)} for every variable
        that appeared or changed between the two points.
        """
        a = self.locals_at(pos_a)
        b = self.locals_at(pos_b)
        result = {}
        for name in set(a) | set(b):
            va = a.get(name)
            vb = b.get(name)
            if va != vb:
                result[name] = (va, vb)
        return result

    def search(self, name: str, value: Any = None, predicate=None) -> list:
        """
        Return indices of frames where a local variable satisfies a condition.

        search("x", 5)                         → frames where x == 5
        search("x", predicate=lambda v: v is None) → frames where x is None
        """
        results = []
        for i, frame in enumerate(self.frames):
            if name not in frame.locals:
                continue
            val = _deserialize_value(frame.locals[name])
            try:
                if predicate is not None:
                    if predicate(val):
                        results.append(i)
                elif val == value:
                    results.append(i)
            except Exception:
                pass
        return results

    def __len__(self) -> int:
        return len(self.frames)

    def __repr__(self) -> str:
        return f"<Replayer {len(self.frames)} frames, pos={self._pos}>"
