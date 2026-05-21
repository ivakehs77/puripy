import linecache
import os
import sys
import runpy

import click

from puripy.recorder import Recorder
from puripy.replayer import Replayer, _deserialize_value, _format_value


# ---------------------------------------------------------------------------
# Shared display helpers
# ---------------------------------------------------------------------------

def _source_context(filename: str, lineno: int, context: int = 2) -> str:
    lines = []
    for n in range(lineno - context, lineno + context + 1):
        src = linecache.getline(filename, n)
        if not src:
            continue
        marker = "→" if n == lineno else " "
        lines.append(f"  {marker} {n:4d} │ {src.rstrip()}")
    return "\n".join(lines)


_BORING_REPR_PREFIXES = ("<function ", "<built-in function ", "<module ", "<class ")


def _is_boring(raw_val) -> bool:
    """True for function/module/class objects that clutter the locals display."""
    if not isinstance(raw_val, dict):
        return False
    r = raw_val.get("__repr__", "")
    return any(r.startswith(p) for p in _BORING_REPR_PREFIXES)


def _print_state(r: Replayer) -> None:
    frame = r.current
    click.echo()
    click.echo(
        f"  [frame {r.pos}/{len(r) - 1} | step {r.line_step}/{r.total_line_steps - 1}"
        f" | +{frame.t:.3f}s]  {frame.func}() @ line {frame.lineno}  ({frame.event})"
    )

    src = _source_context(frame.filename, frame.lineno)
    if src:
        click.echo(src)

    if frame.exc:
        click.echo(f"\n  ! {frame.exc['type']}: {frame.exc['value']}")

    interesting = {
        k: v for k, v in frame.locals.items() if not _is_boring(v)
    }
    if interesting:
        click.echo()
        for name, raw in interesting.items():
            val = _deserialize_value(raw)
            click.echo(f"    {name} = {_format_value(val)}")
    click.echo()


# ---------------------------------------------------------------------------
# REPL command dispatcher
# ---------------------------------------------------------------------------

_HELP = """\
Commands:
  step [N]            advance N source lines (step into calls)
  back [N]            go back N source lines
  over                step over (skip sub-calls, stay in current function)
  out                 step out (run until current function returns)
  goto line N         jump to first execution of source line N
  goto frame N        jump to raw frame index N
  show VAR            print value of a variable at current frame
  locals              print all locals at current frame
  diff line A line B  compare variable state between two source lines
  find VAR VALUE      jump to first frame where VAR == VALUE
  find VAR is None    jump to first frame where VAR is None
  find VAR is not None  jump to first frame where VAR is not None
  why QUESTION        ask the AI to explain something about the execution
  where               show current position
  list                show source context (wider)
  help / ?            show this help
  quit / q            exit
"""


def _parse_find_value(tokens: list):
    """
    Parse the value/predicate part of a `find VAR ...` command.
    Returns (value, predicate) — exactly one will be non-None.
    """
    joined = " ".join(tokens)

    if joined == "is None":
        return None, lambda v: v is None
    if joined == "is not None":
        return None, lambda v: v is not None

    # Single token: try to parse as a Python literal
    if len(tokens) == 1:
        t = tokens[0]
        if t == "None":
            return None, lambda v: v is None
        if t == "True":
            return True, None
        if t == "False":
            return False, None
        try:
            return int(t), None
        except ValueError:
            pass
        try:
            return float(t), None
        except ValueError:
            pass
        # Bare string (strip quotes if present)
        if (t.startswith('"') and t.endswith('"')) or (t.startswith("'") and t.endswith("'")):
            return t[1:-1], None
        return t, None  # treat as bare string

    raise ValueError(f"Cannot parse value: {joined}")


def _run_repl(r: Replayer) -> None:
    try:
        import readline  # enables arrow-key history on most platforms
    except ImportError:
        pass

    _print_state(r)

    while True:
        try:
            raw = input("puripy> ").strip()
        except (EOFError, KeyboardInterrupt):
            click.echo()
            break

        if not raw:
            continue

        parts = raw.split()
        cmd = parts[0].lower()

        try:
            if cmd in ("quit", "q", "exit"):
                break

            elif cmd in ("help", "?"):
                click.echo(_HELP)

            elif cmd == "step":
                n = int(parts[1]) if len(parts) > 1 else 1
                r.step_line(n)
                _print_state(r)

            elif cmd == "back":
                n = int(parts[1]) if len(parts) > 1 else 1
                r.back_line(n)
                _print_state(r)

            elif cmd == "over":
                r.step_over()
                _print_state(r)

            elif cmd == "out":
                r.step_out()
                _print_state(r)

            elif cmd == "goto":
                if len(parts) < 3:
                    click.echo("Usage: goto line N  or  goto frame N")
                    continue
                kind, n = parts[1].lower(), int(parts[2])
                if kind == "line":
                    r.goto_line(n)
                elif kind == "frame":
                    r.goto(n)
                else:
                    click.echo(f"Unknown goto target '{kind}'. Use 'line' or 'frame'.")
                    continue
                _print_state(r)

            elif cmd == "show":
                if len(parts) < 2:
                    click.echo("Usage: show VAR")
                    continue
                name = parts[1]
                val = r.show(name)
                if val is None and name not in r.current.locals:
                    # Check if the variable appears in the next few frames (common if
                    # we're on the assignment line itself, which hasn't run yet).
                    lookahead = min(r.pos + 5, len(r) - 1)
                    found_at = None
                    for i in range(r.pos + 1, lookahead + 1):
                        if name in r.frames[i].locals:
                            found_at = r.frames[i].lineno
                            break
                    hint = f" (assigned on line {found_at})" if found_at else ""
                    click.echo(f"  '{name}' not in scope at this frame{hint}")
                else:
                    click.echo(f"  {name} = {_format_value(val, max_len=500)}")

            elif cmd == "locals":
                interesting = {
                    k: v for k, v in r.current.locals.items() if not _is_boring(v)
                }
                if not interesting:
                    click.echo("  (no locals)")
                else:
                    for name, raw in interesting.items():
                        click.echo(f"  {name} = {_format_value(_deserialize_value(raw))}")

            elif cmd == "diff":
                # diff line A line B
                if len(parts) != 5 or parts[1] != "line" or parts[3] != "line":
                    click.echo("Usage: diff line A line B")
                    continue
                la, lb = int(parts[2]), int(parts[4])
                try:
                    pos_a = r._line_index[la][0]
                    pos_b = r._line_index[lb][0]
                except KeyError as e:
                    click.echo(f"  Line {e} was never executed")
                    continue
                changes = r.diff(pos_a, pos_b)
                if not changes:
                    click.echo(f"  No variable changes between line {la} and line {lb}")
                else:
                    click.echo(f"  Changes from line {la} to line {lb}:")
                    for name, (va, vb) in sorted(changes.items()):
                        before = _format_value(va) if va is not None else "<not in scope>"
                        after  = _format_value(vb) if vb is not None else "<not in scope>"
                        click.echo(f"    {name}: {before} → {after}")

            elif cmd == "find":
                if len(parts) < 3:
                    click.echo("Usage: find VAR VALUE  (or: find VAR is None)")
                    continue
                name = parts[1]
                try:
                    value, predicate = _parse_find_value(parts[2:])
                except ValueError as e:
                    click.echo(f"  Error: {e}")
                    continue
                hits = r.search(name, value=value, predicate=predicate)
                if not hits:
                    click.echo(f"  '{name}' never matches that condition")
                else:
                    click.echo(f"  Found {len(hits)} frame(s). Jumping to first hit.")
                    r.goto(hits[0])
                    _print_state(r)

            elif cmd == "why":
                question = " ".join(parts[1:])
                if not question:
                    click.echo("Usage: why QUESTION")
                    continue
                try:
                    from puripy.ai import ask
                    click.echo("  Thinking...\n")
                    answer = ask(r, question)
                    click.echo(answer)
                except EnvironmentError as e:
                    click.echo(f"  {e}")
                    click.echo("  Tip: export PURIPY_GEMINI_API_KEY=<your-key>")
                except ImportError as e:
                    click.echo(f"  {e}")
                except Exception as e:
                    click.echo(f"  AI error: {e}")

            elif cmd == "where":
                f = r.current
                click.echo(
                    f"  frame {r.pos}/{len(r) - 1}  |  {f.func}()  |  "
                    f"line {f.lineno}  |  {f.filename}"
                )

            elif cmd == "list":
                f = r.current
                src = _source_context(f.filename, f.lineno, context=5)
                click.echo(src if src else "  (source not available)")

            else:
                click.echo(f"  Unknown command '{cmd}'. Type 'help' for commands.")

        except (ValueError, IndexError) as e:
            click.echo(f"  Error: {e}")


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------

@click.group()
def main():
    """PuriPy — time-travel debugging for Python."""
    pass


@main.command()
@click.argument("script", type=click.Path(exists=True))
@click.option("--output", "-o", default=None, help="Output .trace file (default: <script>.trace)")
@click.argument("script_args", nargs=-1)
def record(script: str, output: str, script_args: tuple) -> None:
    """Record a Python script's full execution."""
    script = os.path.abspath(script)

    if output is None:
        base = os.path.splitext(os.path.basename(script))[0]
        output = f"{base}.trace"

    click.echo(f"Recording {os.path.basename(script)}...")

    old_argv = sys.argv
    sys.argv = [script] + list(script_args)

    recorder = Recorder(output_path=output, target_file=script)
    recorder.start()
    try:
        runpy.run_path(script, run_name="__main__")
    except SystemExit:
        pass
    finally:
        recorder.stop()
        sys.argv = old_argv

    size_kb = os.path.getsize(output) / 1024
    click.echo(f"Recorded {len(recorder.frames)} frames -> {output} ({size_kb:.1f} KB)")


@main.command()
@click.argument("trace_file", type=click.Path(exists=True))
@click.option("--tui", is_flag=True, default=False, help="Launch the Textual TUI instead of the REPL.")
def replay(trace_file: str, tui: bool) -> None:
    """Interactively replay a recorded execution."""
    r = Replayer.load(trace_file)
    if r._line_positions:
        r._pos = r._line_positions[0]

    if tui:
        from puripy.tui import PuriPyTUI
        PuriPyTUI(r).run()
    else:
        click.echo(f"Loaded {len(r)} frames from {os.path.basename(trace_file)}")
        click.echo("Type 'help' for commands, 'quit' to exit.")
        _run_repl(r)


@main.command()
@click.argument("trace_file", type=click.Path(exists=True))
def stats(trace_file: str) -> None:
    """Show statistics about a recorded trace."""
    import zlib as _zlib
    import msgpack as _msgpack

    with open(trace_file, "rb") as f:
        compressed = f.read()
    uncompressed = _zlib.decompress(compressed)
    data = _msgpack.unpackb(uncompressed, raw=False)

    frames = data["frames"]
    by_event: dict = {}
    by_func: dict = {}
    total_vars = 0

    for fr in frames:
        by_event[fr["event"]] = by_event.get(fr["event"], 0) + 1
        by_func[fr["func"]] = by_func.get(fr["func"], 0) + 1
        total_vars += len(fr.get("locals", {}))

    duration = frames[-1]["t"] - frames[0]["t"] if frames else 0.0
    ratio = len(uncompressed) / len(compressed) if compressed else 1.0

    click.echo(f"\nTrace: {os.path.basename(trace_file)}")
    click.echo(f"  Format version : {data.get('version', 1)}")
    click.echo(f"  Total frames   : {len(frames)}")
    click.echo(f"  Line events    : {by_event.get('line', 0)}")
    click.echo(f"  Call/Return    : {by_event.get('call', 0)} / {by_event.get('return', 0)}")
    click.echo(f"  Exceptions     : {by_event.get('exception', 0)}")
    click.echo(f"  Execution time : {duration * 1000:.1f} ms")
    click.echo(f"  File size      : {len(compressed) / 1024:.1f} KB compressed"
               f"  /  {len(uncompressed) / 1024:.1f} KB raw  (ratio {ratio:.1f}x)")
    click.echo(f"  Var entries    : {total_vars} total across all frames")
    click.echo(f"\n  Hot functions (by frame count):")
    for func, count in sorted(by_func.items(), key=lambda x: -x[1])[:8]:
        click.echo(f"    {count:5d}  {func}")


@main.command()
@click.argument("trace_file", type=click.Path(exists=True))
@click.argument("question")
@click.option("--model", default="gemini-2.5-flash-lite", show_default=True,
              help="Gemini model to use.")
def ask(trace_file: str, question: str, model: str) -> None:
    """Ask the AI a question about a recorded execution.

    Requires PURIPY_GEMINI_API_KEY (or GEMINI_API_KEY) to be set.

    Example: puripy ask script.trace "why is grade always F?"
    """
    from puripy.ai import ask as _ask

    r = Replayer.load(trace_file)
    click.echo(f"Analyzing {os.path.basename(trace_file)} ({len(r)} frames)...")
    click.echo()

    try:
        answer = _ask(r, question, model=model)
        click.echo(answer)
    except EnvironmentError as e:
        click.echo(f"Error: {e}", err=True)
        click.echo("Set PURIPY_GEMINI_API_KEY=<your-key> and try again.", err=True)
        raise SystemExit(1)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        raise SystemExit(1)
