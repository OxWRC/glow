"""Convert terraform/packer ANSI-colored output into HTML for the job log.

Subprocess output (job.lines) can carry SGR escape sequences when the
child's color detection is fooled by an inherited CLICOLOR_FORCE-style env
var even though stdout is piped, not a tty. Rather than stripping colour,
turn it into <span> markup so the browser renders it the way a terminal would.
"""

from __future__ import annotations

import html
import re

_SGR_RE = re.compile(r"\x1b\[([0-9;]*)m")

_FG_NAMES = {
    30: "black",
    31: "red",
    32: "green",
    33: "yellow",
    34: "blue",
    35: "magenta",
    36: "cyan",
    37: "white",
}
_FG_BRIGHT_NAMES = {code + 60: f"bright-{name}" for code, name in _FG_NAMES.items()}


def ansi_to_html(text: str) -> str:
    """Escape ``text`` and translate ANSI SGR codes into ``<span>`` tags."""
    state: dict[str, str] = {}
    open_span = False
    out: list[str] = []
    pos = 0

    def close_span() -> None:
        nonlocal open_span
        if open_span:
            out.append("</span>")
            open_span = False

    def open_span_if_needed() -> None:
        nonlocal open_span
        if state and not open_span:
            out.append(f'<span class="{" ".join(state.values())}">')
            open_span = True

    for match in _SGR_RE.finditer(text):
        out.append(html.escape(text[pos : match.start()]))
        pos = match.end()

        close_span()
        codes = [int(c) for c in match.group(1).split(";") if c] or [0]
        for code in codes:
            if code == 0:
                state.clear()
            elif code == 1:
                state["bold"] = "ansi-bold"
            elif code == 4:
                state["underline"] = "ansi-underline"
            elif code == 22:
                state.pop("bold", None)
            elif code == 24:
                state.pop("underline", None)
            elif code == 39:
                state.pop("fg", None)
            elif code in _FG_NAMES:
                state["fg"] = f"ansi-fg-{_FG_NAMES[code]}"
            elif code in _FG_BRIGHT_NAMES:
                state["fg"] = f"ansi-fg-{_FG_BRIGHT_NAMES[code]}"
        open_span_if_needed()

    out.append(html.escape(text[pos:]))
    close_span()
    return "".join(out)


def _demo() -> None:
    assert ansi_to_html("plain") == "plain"
    assert (
        ansi_to_html("\x1b[32m+\x1b[0m ok") == '<span class="ansi-fg-green">+</span> ok'
    )
    assert (
        ansi_to_html("\x1b[1mheader\x1b[0m") == '<span class="ansi-bold">header</span>'
    )
    assert ansi_to_html("<script>") == "&lt;script&gt;"


if __name__ == "__main__":
    _demo()
    print("ok")
