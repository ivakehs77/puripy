"""Tests for the Recorder class."""
import os
import zlib

import msgpack
import pytest

from puripy.recorder import Recorder, _serialize_value, _snapshot_locals


class _Point:
    """Module-level class so pickle can find it by import path."""
    def __init__(self, x, y):
        self.x = x
        self.y = y


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_trace(path: str) -> dict:
    with open(path, "rb") as f:
        return msgpack.unpackb(zlib.decompress(f.read()), raw=False)


def record_fn(fn, output_path: str) -> dict:
    """Run fn() under a Recorder writing to output_path, return loaded trace."""
    recorder = Recorder(output_path=output_path)
    recorder.start()
    fn()
    recorder.stop()
    return load_trace(output_path)


# ---------------------------------------------------------------------------
# _serialize_value unit tests
# ---------------------------------------------------------------------------

def test_serialize_primitives():
    assert _serialize_value(None) is None
    assert _serialize_value(True) is True
    assert _serialize_value(42) == 42
    assert _serialize_value(3.14) == 3.14
    assert _serialize_value("hello") == "hello"


def test_serialize_list():
    assert _serialize_value([1, 2, 3]) == [1, 2, 3]


def test_serialize_dict():
    assert _serialize_value({"a": 1}) == {"a": 1}


def test_serialize_bytes():
    result = _serialize_value(b"raw")
    assert "__bytes__" in result


def test_serialize_set():
    result = _serialize_value({1, 2, 3})
    assert "__set__" in result
    assert sorted(result["__set__"]) == [1, 2, 3]


def test_serialize_custom_class_uses_pickle():
    result = _serialize_value(_Point(1, 2))
    assert "__pickle__" in result


def test_serialize_unpicklable_falls_back_to_repr():
    # Lambda functions can't be pickled.
    result = _serialize_value(lambda: None)
    assert "__repr__" in result


# ---------------------------------------------------------------------------
# _snapshot_locals
# ---------------------------------------------------------------------------

def test_snapshot_skips_dunder_names():
    snapshot = _snapshot_locals({"x": 1, "__builtins__": {}, "y": 2})
    assert "x" in snapshot
    assert "y" in snapshot
    assert "__builtins__" not in snapshot


def test_snapshot_handles_undeep_copyable():
    class NoCopy:
        def __deepcopy__(self, memo):
            raise RuntimeError("no copy")

    snapshot = _snapshot_locals({"bad": NoCopy()})
    assert "__repr__" in snapshot["bad"]


# ---------------------------------------------------------------------------
# Recorder integration tests
# ---------------------------------------------------------------------------

def test_basic_recording_produces_file(tmp_path):
    output = str(tmp_path / "out.trace")

    def fn():
        x = 1
        y = x + 1
        return y

    record_fn(fn, output)
    assert os.path.exists(output)
    assert os.path.getsize(output) > 0


def test_trace_has_correct_structure(tmp_path):
    output = str(tmp_path / "out.trace")

    def fn():
        a = 10

    trace = record_fn(fn, output)
    assert trace["version"] == 2
    assert "frames" in trace
    assert isinstance(trace["frames"], list)
    assert len(trace["frames"]) > 0


def test_variable_captured_in_locals(tmp_path):
    output = str(tmp_path / "out.trace")

    def fn():
        my_var = 99
        return my_var

    trace = record_fn(fn, output)
    line_frames = [f for f in trace["frames"] if f["event"] == "line"]
    # At some point after assignment, my_var should appear
    locals_with_var = [f for f in line_frames if "my_var" in f["locals"]]
    assert len(locals_with_var) > 0
    assert locals_with_var[0]["locals"]["my_var"] == 99


def test_return_value_captured(tmp_path):
    output = str(tmp_path / "out.trace")

    def fn():
        return 42

    trace = record_fn(fn, output)
    return_frames = [f for f in trace["frames"] if f["event"] == "return"]
    assert any(f.get("retval") == 42 for f in return_frames)


def test_exception_captured(tmp_path):
    output = str(tmp_path / "out.trace")

    def fn():
        try:
            raise ValueError("oops")
        except ValueError:
            pass

    trace = record_fn(fn, output)
    exc_frames = [f for f in trace["frames"] if f["event"] == "exception"]
    assert len(exc_frames) > 0
    assert exc_frames[0]["exc"]["type"] == "ValueError"
    assert exc_frames[0]["exc"]["value"] == "oops"


def test_mutation_captured_via_deep_copy(tmp_path):
    output = str(tmp_path / "out.trace")

    captured = []

    def fn():
        items = []
        items.append(1)
        items.append(2)

    trace = record_fn(fn, output)
    # Find the frame right after append(1) — items should be [1], not [1, 2]
    line_frames = [f for f in trace["frames"] if f["event"] == "line" and "items" in f["locals"]]
    values = [f["locals"]["items"] for f in line_frames]
    # Because we deep copy, we should see [] before [], then [1], then [1, 2]
    assert [] in values or [1] in values  # intermediate states were captured


def test_stdlib_not_traced(tmp_path):
    output = str(tmp_path / "out.trace")

    def fn():
        import json
        json.dumps({"x": 1})

    trace = record_fn(fn, output)
    for frame in trace["frames"]:
        assert "lib/python" not in frame["filename"]
        assert "site-packages" not in frame["filename"]


def test_context_manager(tmp_path):
    output = str(tmp_path / "out.trace")

    with Recorder(output_path=output) as r:
        x = 1 + 1

    assert os.path.exists(output)
    assert len(r.frames) > 0


def test_trace_is_compressed(tmp_path):
    output = str(tmp_path / "out.trace")

    def fn():
        for i in range(100):
            x = i * 2

    record_fn(fn, output)
    raw_size = os.path.getsize(output)
    # Decompress and repack to compare sizes
    with open(output, "rb") as f:
        uncompressed = msgpack.packb(
            msgpack.unpackb(zlib.decompress(f.read()), raw=False),
            use_bin_type=True,
        )
    assert raw_size < len(uncompressed)


# ---------------------------------------------------------------------------
# Delta compression
# ---------------------------------------------------------------------------

def test_delta_frames_stored_in_raw_trace(tmp_path):
    output = str(tmp_path / "out.trace")

    def fn():
        a = 1
        b = 2
        a = a + b

    record_fn(fn, output)
    trace = load_trace(output)
    line_frames = [f for f in trace["frames"] if f["event"] == "line"]
    # At least some line frames should be delta-encoded.
    assert any(f.get("delta") for f in line_frames)


def test_delta_reconstruction_gives_full_locals(tmp_path):
    output = str(tmp_path / "out.trace")

    def fn():
        x = 10
        y = 20
        z = x + y
        return z

    record_fn(fn, output)

    from puripy.replayer import Replayer
    r = Replayer.load(output)

    # Find the frame where z is computed (line with z = x + y).
    # After reconstruction every line frame should have full locals.
    line_frames = [f for f in r.frames if f.event == "line" and "z" in f.locals]
    assert len(line_frames) > 0
    # The reconstructed frame should also have x and y, not just z.
    assert "x" in line_frames[0].locals
    assert "y" in line_frames[0].locals
    assert line_frames[0].locals["z"] == 30


def test_delta_reduces_raw_variable_entries(tmp_path):
    """Delta encoding means line frames only store what changed."""
    output = str(tmp_path / "out.trace")

    def fn():
        stable = "unchanged"
        for i in range(20):
            counter = i * 2  # only counter changes each iteration

    record_fn(fn, output)
    trace = load_trace(output)

    delta_frames = [f for f in trace["frames"] if f.get("delta")]
    # 'stable' should appear very few times (only when it first appears),
    # not on every iteration.
    stable_appearances = sum(1 for f in delta_frames if "stable" in f.get("locals", {}))
    total_line_frames = len(delta_frames)
    assert stable_appearances < total_line_frames  # not stored every frame


# ---------------------------------------------------------------------------
# Replayer.search
# ---------------------------------------------------------------------------

def test_search_by_value(tmp_path):
    output = str(tmp_path / "out.trace")

    def fn():
        x = 0
        x = 5
        x = 10

    record_fn(fn, output)
    from puripy.replayer import Replayer
    r = Replayer.load(output)
    hits = r.search("x", 5)
    assert len(hits) > 0
    assert r.frames[hits[0]].locals.get("x") == 5


def test_search_by_predicate(tmp_path):
    output = str(tmp_path / "out.trace")

    def fn():
        result = None
        result = "found"

    record_fn(fn, output)
    from puripy.replayer import Replayer
    r = Replayer.load(output)
    hits = r.search("result", predicate=lambda v: v is None)
    assert len(hits) > 0


def test_search_no_match(tmp_path):
    output = str(tmp_path / "out.trace")

    def fn():
        x = 42

    record_fn(fn, output)
    from puripy.replayer import Replayer
    r = Replayer.load(output)
    assert r.search("x", 999) == []
