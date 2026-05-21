import linecache
import os

from rich.syntax import Syntax
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Footer, Header, Input, Label, Static

from puripy.replayer import Replayer, _deserialize_value, _format_value

_BORING_REPR_PREFIXES = ("<function ", "<built-in function ", "<module ", "<class ")


def _is_boring(raw_val) -> bool:
    if not isinstance(raw_val, dict):
        return False
    r = raw_val.get("__repr__", "")
    return any(r.startswith(p) for p in _BORING_REPR_PREFIXES)


def _code_window(filename: str, lineno: int, context: int = 12) -> Syntax:
    start = max(1, lineno - context)
    lines = []
    for n in range(start, lineno + context + 1):
        src = linecache.getline(filename, n)
        if not src:
            break
        lines.append(src)
    return Syntax(
        "".join(lines),
        "python",
        line_numbers=True,
        start_line=start,
        highlight_lines={lineno},
        theme="monokai",
    )


class CodePanel(Static):
    pass


class AIOutput(Static):
    pass


class PuriPyTUI(App):
    """Time-travel debugger — keyboard-driven TUI."""

    CSS = """
    Screen {
        layout: vertical;
    }

    #body {
        layout: horizontal;
        height: 1fr;
    }

    #code-panel {
        width: 2fr;
        border: round $primary;
        padding: 0 1;
        overflow-y: auto;
    }

    #vars-panel {
        width: 1fr;
        border: round $secondary;
    }

    #status-bar {
        height: 1;
        background: $boost;
        padding: 0 2;
        color: $text-muted;
    }

    #ai-panel {
        height: auto;
        max-height: 12;
        border: round $warning;
        display: none;
    }

    #ai-output {
        height: auto;
        max-height: 8;
        overflow-y: auto;
        padding: 0 1;
    }

    #ai-input-row {
        height: 3;
        layout: horizontal;
        padding: 0 1;
    }

    #ai-label {
        width: auto;
        padding: 1 1 1 0;
        color: $warning;
    }

    #ai-input {
        width: 1fr;
    }
    """

    BINDINGS = [
        Binding("j,right", "step", "Step"),
        Binding("k,left", "back", "Back"),
        Binding("n", "step_over", "Over"),
        Binding("u", "step_out", "Out"),
        Binding("question_mark", "toggle_ai", "Ask AI"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, replayer: Replayer):
        super().__init__()
        self.r = replayer

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical():
            with Horizontal(id="body"):
                yield CodePanel(id="code-panel")
                yield DataTable(id="vars-panel", show_cursor=False)
            yield Static(id="status-bar")
            with Vertical(id="ai-panel"):
                yield AIOutput("", id="ai-output")
                with Horizontal(id="ai-input-row"):
                    yield Label("Ask AI:", id="ai-label")
                    yield Input(placeholder="why is grade always F?", id="ai-input")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#vars-panel", DataTable)
        table.add_columns("Variable", "Value")
        self._refresh()

    def _refresh(self) -> None:
        r = self.r
        frame = r.current
        filename = os.path.basename(frame.filename)

        self.title = (
            f"PuriPy  |  {filename}  |  {frame.func}()"
            f"  |  step {r.line_step + 1}/{r.total_line_steps}"
            f"  |  depth {r.depth}"
        )

        self.query_one("#code-panel", CodePanel).update(
            _code_window(frame.filename, frame.lineno)
        )

        table = self.query_one("#vars-panel", DataTable)
        table.clear()
        interesting = {k: v for k, v in frame.locals.items() if not _is_boring(v)}
        for name, raw in interesting.items():
            val = _deserialize_value(raw)
            table.add_row(name, _format_value(val, max_len=50))

        exc = frame.exc
        if exc:
            status = f"[bold red]{exc['type']}[/]: {exc['value']}"
        else:
            status = (
                f"line {frame.lineno}"
                f"  |  step {r.line_step + 1}/{r.total_line_steps}"
                f"  |  +{frame.t:.3f}s"
                f"  |  [dim]j/→ fwd  k/← back  n over  u out  ? AI  q quit[/]"
            )
        self.query_one("#status-bar", Static).update(status)

    # ------------------------------------------------------------------
    # Navigation actions
    # ------------------------------------------------------------------

    def action_step(self) -> None:
        self.r.step_line()
        self._refresh()

    def action_back(self) -> None:
        self.r.back_line()
        self._refresh()

    def action_step_over(self) -> None:
        self.r.step_over()
        self._refresh()

    def action_step_out(self) -> None:
        self.r.step_out()
        self._refresh()

    # ------------------------------------------------------------------
    # AI panel
    # ------------------------------------------------------------------

    def action_toggle_ai(self) -> None:
        panel = self.query_one("#ai-panel")
        panel.display = not panel.display
        if panel.display:
            self.query_one("#ai-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        question = event.value.strip()
        if not question:
            return
        event.input.value = ""
        self.run_worker(self._ask_ai(question), exclusive=True)

    def on_key(self, event) -> None:
        if event.key == "escape":
            panel = self.query_one("#ai-panel")
            if panel.display:
                panel.display = False
                event.stop()

    async def _ask_ai(self, question: str) -> None:
        import asyncio
        output = self.query_one("#ai-output", AIOutput)
        output.update("Thinking...")
        try:
            from puripy.ai import ask
            answer = await asyncio.get_event_loop().run_in_executor(
                None, lambda: ask(self.r, question)
            )
            output.update(answer)
        except EnvironmentError:
            output.update("[red]No API key — set PURIPY_GEMINI_API_KEY[/]")
        except ImportError:
            output.update("[red]Run: pip install 'puripy[ai]'[/]")
        except Exception as e:
            output.update(f"[red]Error: {e}[/]")
