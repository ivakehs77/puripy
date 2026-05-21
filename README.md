# PuriPy

> Time-travel debugging for Python. Record every line of execution, then rewind and replay it.

Standard debuggers only move forward. PuriPy records your entire program run up front, then gives you a timeline you can scrub in both directions — jump to any line, inspect any variable at any point in time, and ask an AI to explain what went wrong.

```
$ puripy record buggy_script.py
Recording buggy_script.py...
Recorded 1,247 frames → buggy_script.trace (4.2 KB)

$ puripy replay buggy_script.trace
```

```
  [frame 23/79 | step 19/65 | +0.001s]  main() @ line 41

      39 │     for name, score in students.items():
      40 │         grade = calculate_grade(score, 100)
  →   41 │         results[name] = {"score": score, "grade": grade}

    name  = Alice
    score = 92
    grade = F           ← why is this F?

puripy> why is grade always F?
  Thinking...

  Root cause: Line 9 computes `percentage = score / total * 10` — it
  should be `* 100`. For score=92 this gives 9.2, which falls below
  every grade threshold and always returns "F".

  Fix: change line 9 to:
      percentage = score / total * 100
```

![PuriPy demo](demo.gif)

---

## Quick Start

```bash
# 1. Install
git clone https://github.com/ivakehs77/puripy
cd puripy
python -m venv .venv && source .venv/bin/activate
pip install -e ".[ai]"

# 2. Record the included demo script
puripy record examples/demo.py

# 3. Replay it interactively
puripy replay demo.trace
```

Once inside the REPL:
```
puripy> goto line 40       # jump to the buggy line
puripy> step               # step into calculate_grade()
puripy> show percentage    # see 9.2 instead of 92.0
puripy> out                # step back out to main
puripy> find grade F       # find every frame where grade is wrong
puripy> quit
```

With an AI key ([get one free](https://aistudio.google.com)):
```bash
export PURIPY_GEMINI_API_KEY=your-key
puripy ask demo.trace "why is grade always F?"
```

---

## PuriPy vs pdb vs print debugging

| | `print()` | `pdb` | **PuriPy** |
|---|---|---|---|
| Go backwards | No | No | **Yes** |
| Re-run to inspect a different line | Yes | Yes | **No — everything already recorded** |
| Interrupts your program | No | Yes | **No** |
| See variable history across the whole run | No | No | **Yes** |
| Jump to any line instantly | No | No | **Yes** |
| Step over / step out | No | Yes | **Yes** |
| AI explains the bug | No | No | **Yes** |
| Works on already-finished runs | No | No | **Yes** |

The core difference: `pdb` is a *live* debugger — you set breakpoints before running and step forward from there. If you miss something, you restart. PuriPy *records first, asks questions later* — run your script once, then scrub the full execution timeline in any direction without touching the code.

---

## How it works

Python exposes `sys.settrace` — a hook that fires before every line executes, on every function call, and on every return. PuriPy installs a tracer that snapshots `frame.f_locals` at each event and writes it to a compressed binary trace file.

Three things make this non-trivial:

**Delta encoding.** Storing full variable snapshots every line gets expensive fast. PuriPy instead stores the full snapshot on `call` events (function entry) and only the *changed* variables on subsequent `line` events. The replayer reconstructs full state by replaying deltas forward from the nearest call frame. A function with 10 locals that mutates 1 variable per line goes from 500 stored entries to ~60.

**Serialization pipeline.** `f_locals` can contain anything — file handles, custom classes, lambdas. Functions, modules, and types are stored as their `repr()` string immediately (pickling them fails at replay time since the script isn't importable by its original module path). Everything else goes through `pickle`, falling back to `repr()` if that fails. Deep copy at capture time ensures list mutations are recorded correctly rather than all pointing to the final state.

**O(1) navigation.** The replayer pre-computes a line index and call-depth table at load time so `goto line 47` and `step_over` are instant regardless of trace size.

---

## Install

```bash
git clone https://github.com/abishekpuri/puripy
cd puripy
python -m venv .venv && source .venv/bin/activate
pip install -e .          # core
pip install -e ".[ai]"    # + AI query layer
```

Python 3.9+.

---

## CLI

### Record

```bash
puripy record script.py
puripy record script.py -o custom.trace
puripy record script.py -- arg1 arg2    # pass args to the script
```

### Replay

```bash
puripy replay script.trace        # interactive REPL
puripy replay script.trace --tui  # Textual TUI (source + variables panels)
```

In the TUI: `j`/`→` step forward, `k`/`←` step back, `n` step over, `u` step out, `?` open the AI query panel, `q` quit.

![PuriPy TUI demo](demo_tui.gif)

#### REPL commands

| Command | What it does |
|---|---|
| `step [N]` | Advance N source lines (steps into calls) |
| `back [N]` | Go back N source lines |
| `over` | Step over — skip sub-calls, stay in current function |
| `out` | Step out — run until current function returns |
| `goto line N` | Jump to first execution of source line N |
| `goto frame N` | Jump to raw frame index |
| `show VAR` | Print variable value at current frame |
| `locals` | Print all locals at current frame |
| `diff line A line B` | Compare variable state between two source lines |
| `find VAR VALUE` | Jump to first frame where VAR equals VALUE |
| `find VAR is None` | Jump to first frame where VAR is None |
| `why QUESTION` | Ask the AI to explain something |
| `where` | Show current position |
| `list` | Show wider source context |

### Ask (AI)

```bash
export PURIPY_GEMINI_API_KEY=your-key
puripy ask script.trace "why is result always None?"
```

Get a free key at [aistudio.google.com](https://aistudio.google.com).

### Stats

```bash
puripy stats script.trace
```

```
  Total frames   : 1,247
  Line events    : 1,031
  Execution time : 18.4 ms
  File size      : 4.2 KB compressed  /  38.1 KB raw  (ratio 9.1x)

  Hot functions:
       412  process_records
       308  validate_input
```

---

## Architecture

```
src/puripy/
├── recorder.py   sys.settrace hook, delta encoder, msgpack+zlib writer
├── replayer.py   trace loader, delta reconstruction, O(1) navigation
├── cli.py        Click commands: record / replay / ask / stats
├── tui.py        Textual TUI (source + variables panels, inline AI query)
└── ai.py         context extraction from trace → Gemini prompt → answer
```

The trace format is `zlib(msgpack({"version": 2, "frames": [...]}))`. Each frame is a plain dict — easy to inspect with any msgpack library.

---

## Limitations

- Single-threaded Python only (no `threading`, `asyncio`, `multiprocessing`)
- C extension internals are opaque (NumPy, pandas operations appear as black boxes)
- Designed for scripts up to ~10,000 lines / 30 seconds of execution

---

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=ivakehs77/puripy&type=Date)](https://star-history.com/#ivakehs77/puripy&Date)

---

## Inspiration

- [rr](https://rr-project.org/) — record/replay for C/C++
- [Replay.io](https://replay.io) — time-travel debugging for browsers  
- [PySnooper](https://github.com/cool-RR/PySnooper) — lightweight Python tracing

---

Built by [Abishek Puri](https://github.com/abishekpuri) · CS @ Texas State University
