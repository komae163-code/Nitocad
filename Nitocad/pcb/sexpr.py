"""Tiny KiCad s-expression reader/writer (enough for .kicad_mod/.kicad_pcb)."""
from __future__ import annotations


class Q(str):
    """A quoted string atom."""


def parse(text: str):
    """Parse the first top-level expression in `text` into nested lists."""
    pos = 0
    n = len(text)

    def skip_ws():
        nonlocal pos
        while pos < n and text[pos] in " \t\r\n":
            pos += 1

    def read():
        nonlocal pos
        skip_ws()
        if pos >= n:
            raise ValueError("unexpected end of s-expression")
        c = text[pos]
        if c == "(":
            pos += 1
            out = []
            while True:
                skip_ws()
                if pos >= n:
                    raise ValueError("unbalanced parentheses")
                if text[pos] == ")":
                    pos += 1
                    return out
                out.append(read())
        if c == '"':
            pos += 1
            buf = []
            while pos < n and text[pos] != '"':
                if text[pos] == "\\" and pos + 1 < n:
                    pos += 1
                buf.append(text[pos])
                pos += 1
            pos += 1
            return Q("".join(buf))
        start = pos
        while pos < n and text[pos] not in " \t\r\n()":
            pos += 1
        return text[start:pos]

    return read()


def _atom(a) -> str:
    if isinstance(a, Q):
        return '"' + str(a).replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(a, float):
        s = f"{a:.6f}".rstrip("0").rstrip(".")
        return "0" if s in ("-0", "") else s
    return str(a)


def dump(node, indent: int = 0) -> str:
    if not isinstance(node, list):
        return _atom(node)
    if not any(isinstance(c, list) for c in node):
        return "(" + " ".join(_atom(c) for c in node) + ")"
    pad = "  " * (indent + 1)
    head = [c for c in node if not isinstance(c, list)]
    # keep leading atoms on the first line, children on their own lines
    first = []
    i = 0
    while i < len(node) and not isinstance(node[i], list):
        first.append(_atom(node[i]))
        i += 1
    lines = ["(" + " ".join(first)]
    for c in node[i:]:
        lines.append(pad + dump(c, indent + 1))
    return "\n".join(lines) + ")"


def find(node, name):
    """First direct child list whose head atom == name."""
    for c in node:
        if isinstance(c, list) and c and c[0] == name:
            return c
    return None


def find_all(node, name):
    return [c for c in node if isinstance(c, list) and c and c[0] == name]
