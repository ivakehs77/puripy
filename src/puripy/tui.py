import linecache
import os

from rich.syntax import Syntax
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Footer, Header, Static

from puripy.replayer import Replayer, _deserialize_value, _format_value

_BORING_TYPES = ("function", "builtin_function_or_method", "module", "type")


def _is_boring(raw_val) -> bool:
    if not isinstance(raw_val, dict):
        return False
    if raw_val.get("__type__", "") in _BORING_TYPES:
        return True
    r = raw_val.get("__repr__", "")
    return any(r.startswith(t + ":") for t in _BORING_TYPES)


def _code_window(filename: str, lineno: int, context: int = 12) -> Syntax:
    """Return a Rich Syntax object showing source around lineno."""
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
    """

    BINDINGS = [
        Binding("j,right", "step", "Step"),
        Binding("k,left", "back", "Back"),
        Binding("n", "step_over", "Over"),
        Binding("u", "step_out", "Out"),
        Binding("q,escape", "quit", "Quit"),
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
            f"PuriPy  |  {filename}  |  {frame.func}()  |  "
            f"frame {r.pos}/{len(r) - 1}  |  depth {r.depth}"
        )

        # Code panel
        self.query_one("#code-panel", CodePanel).update(
            _code_window(frame.filename, frame.lineno)
        )

        # Variables table
        table = self.query_one("#vars-panel", DataTable)
        table.clear()
        interesting = {k: v for k, v in frame.locals.items() if not _is_boring(v)}
        for name, raw in interesting.items():
            val = _deserialize_value(raw)
            table.add_row(name, _format_value(val, max_len=50))

        # Status bar
        exc = frame.exc
        if exc:
            status = f"[bold red]{exc['type']}[/]: {exc['value']}"
        else:
            status = (
                f"line {frame.lineno}  |  step {r.line_step}/{r.total_line_steps - 1}"
                f"  |  +{frame.t:.3f}s  |  [dim]j/→ step  k/← back  n over  u out  q quit[/]"
            )
        self.query_one("#status-bar", Static).update(status)

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
