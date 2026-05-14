"""
AI query layer for PuriPy.

Given a loaded trace and a natural-language question, this module:
  1. Extracts relevant context from the trace (variable histories, source code,
     execution path) — a lightweight RAG step over the execution timeline.
  2. Assembles a detailed prompt.
  3. Sends it to Gemini and streams back the answer.

Usage:
    from puripy.ai import ask
    answer = ask(replayer, "why is grade always F?", api_key="...")
"""

import ast
import linecache
import os
import re
from typing import Any, Optional

from puripy.replayer import Replayer, _deserialize_value, _format_value

# ---------------------------------------------------------------------------
# Context extraction helpers
# ---------------------------------------------------------------------------

_QUERY_STOP_WORDS = {
    "why", "is", "the", "a", "an", "in", "at", "for", "of", "and", "or",
    "what", "when", "how", "does", "did", "was", "were", "has", "have",
    "had", "be", "been", "always", "never", "not", "none", "true", "false",
    "this", "that", "it", "its", "so", "do", "get", "set", "my", "can",
    "value", "variable", "function", "line", "code",
}


def _names_in_query(query: str, known: set) -> list:
    """Return variable/function names from the query that appear in the trace."""
    tokens = re.findall(r'\b[a-zA-Z_][a-zA-Z0-9_]*\b', query.lower())
    # Prioritise exact matches, then case-insensitive matches
    exact = [t for t in tokens if t in known and t not in _QUERY_STOP_WORDS]
    fuzzy = [k for k in known if k.lower() in tokens and k not in exact]
    seen = set()
    result = []
    for name in exact + fuzzy:
        if name not in seen:
            seen.add(name)
            result.append(name)
    return result


def _all_variable_names(r: Replayer) -> set:
    """Collect every variable name that appears anywhere in the trace."""
    names: set = set()
    for frame in r.frames:
        names.update(frame.locals.keys())
    return names


def _all_function_names(r: Replayer) -> set:
    return {f.func for f in r.frames}


def _variable_history(r: Replayer, name: str, max_entries: int = 15) -> list:
    """
    Return (frame_idx, func, lineno, value) each time a variable's value
    changes — the core of the 'what happened to X?' query.
    """
    history = []
    _sentinel = object()
    last_val = _sentinel
    for i, frame in enumerate(r.frames):
        if name not in frame.locals:
            continue
        val = _deserialize_value(frame.locals[name])
        try:
            changed = val != last_val
        except Exception:
            changed = True
        if changed or last_val is _sentinel:
            history.append((i, frame.func, frame.lineno, val))
            last_val = val
    return history[:max_entries]


def _function_source(filename: str, func_name: str) -> str:
    """Extract a function's source via AST for accurate line boundaries."""
    if not filename or not os.path.exists(filename):
        return ""
    try:
        with open(filename) as f:
            src = f.read()
        tree = ast.parse(src)
        lines = src.splitlines()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name == func_name:
                    start = node.lineno - 1
                    end = getattr(node, "end_lineno", start + 20)
                    return "\n".join(
                        f"  {start + i + 1:4d} | {lines[start + i]}"
                        for i in range(end - start)
                        if start + i < len(lines)
                    )
    except Exception:
        pass
    return ""


def _execution_path(r: Replayer, max_frames: int = 20) -> str:
    """
    Condensed execution timeline: function transitions + exception events.
    Skips consecutive lines in the same function to keep it readable.
    """
    lines = []
    last_func = None
    exception_indices = {i for i, f in enumerate(r.frames) if f.event == "exception"}

    for i, frame in enumerate(r.frames):
        if frame.event == "exception":
            lines.append(
                f"  ! EXCEPTION  {frame.func}() line {frame.lineno}: "
                f"{frame.exc['type']}: {frame.exc['value']}"
            )
        elif frame.event in ("call", "return"):
            arrow = "→" if frame.event == "call" else "←"
            lines.append(f"  {arrow} {frame.func}() line {frame.lineno}")
            last_func = frame.func
        elif frame.event == "line" and frame.func != last_func:
            lines.append(f"    {frame.func}() line {frame.lineno}")
            last_func = frame.func

    if len(lines) > max_frames:
        half = max_frames // 2
        lines = lines[:half] + [f"  ... ({len(lines) - max_frames} more) ..."] + lines[-half:]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------

def build_prompt(r: Replayer, query: str) -> str:
    """
    Assemble a detailed prompt giving Gemini everything it needs to answer
    the user's question about the recorded execution.
    """
    target = r.target or "unknown"
    basename = os.path.basename(target)

    known_vars = _all_variable_names(r)
    known_funcs = _all_function_names(r)
    mentioned = _names_in_query(query, known_vars | known_funcs)

    # Execution summary
    line_events = sum(1 for f in r.frames if f.event == "line")
    funcs_called = sorted(known_funcs - {"<module>"})
    duration_ms = (r.frames[-1].t - r.frames[0].t) * 1000 if r.frames else 0

    summary = (
        f"  File:           {basename}\n"
        f"  Total frames:   {len(r.frames)} ({line_events} line events)\n"
        f"  Functions:      {', '.join(funcs_called)}\n"
        f"  Duration:       {duration_ms:.2f} ms"
    )

    # Variable histories for mentioned names
    var_sections = []
    for name in mentioned:
        if name not in known_vars:
            continue
        history = _variable_history(r, name)
        if not history:
            continue
        rows = "\n".join(
            f"    [frame {idx:3d}] {func}() line {lineno}: {_format_value(val, max_len=80)}"
            for idx, func, lineno, val in history
        )
        var_sections.append(f"  {name}:\n{rows}")

    var_block = "\n\n".join(var_sections) if var_sections else "  (no matching variables found)"

    # Source for every user-defined function in the execution path (skip <module>).
    # Always include all functions so the AI has full context — traces are small.
    source_sections = []
    added_funcs: set = set()
    for frame in r.frames:
        func = frame.func
        if func == "<module>" or func in added_funcs:
            continue
        if not frame.filename or not os.path.exists(frame.filename):
            continue
        src = _function_source(frame.filename, func)
        if src:
            source_sections.append(f"  {func}():\n{src}")
            added_funcs.add(func)

    source_block = "\n\n".join(source_sections) if source_sections else "  (source not available)"

    exec_path = _execution_path(r)

    return f"""\
You are an expert Python debugger analyzing an execution trace recorded by PuriPy.

## Script
{basename}

## Question
"{query}"

## Execution Summary
{summary}

## Variable Histories
(Each entry shows when a variable's value changed during execution.)

{var_block}

## Relevant Source Code
{source_block}

## Execution Path
{exec_path}

## Your Task
Please explain:
1. **Root cause**: What is happening and why?
2. **Location**: Which specific line(s) are responsible?
3. **Fix**: How would you correct this? (include the corrected code)

Reference specific line numbers and variable values from the trace above.
Be concise and precise — this is for a developer actively debugging.\
"""


# ---------------------------------------------------------------------------
# Gemini integration
# ---------------------------------------------------------------------------

def ask(r: Replayer, query: str, api_key: Optional[str] = None, model: str = "gemini-2.5-flash-lite") -> str:
    """
    Ask a natural-language question about the recorded execution.
    Returns the model's answer as a string.

    Requires PURIPY_GEMINI_API_KEY env var or explicit api_key argument.
    """
    key = api_key or os.environ.get("PURIPY_GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not key:
        raise EnvironmentError(
            "No Gemini API key found. Set PURIPY_GEMINI_API_KEY or pass api_key=."
        )

    try:
        from google import genai
        from google.genai import types as genai_types
    except ImportError:
        raise ImportError(
            "google-genai is required for AI queries. "
            "Install it: pip install google-genai"
        )

    prompt = build_prompt(r, query)

    client = genai.Client(api_key=key)
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=genai_types.GenerateContentConfig(
            temperature=0.2,  # low temperature for factual debugging answers
            max_output_tokens=1024,
        ),
    )
    return response.text
