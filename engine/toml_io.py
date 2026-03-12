"""
toml_io.py — Zero-dependency TOML reader and writer for Delve.

Supports the subset of TOML used by this project:
  - Key/value pairs (strings, ints, floats, bools)
  - Array of tables  [[section]]
  - Inline tables    { key = val, ... }
  - Inline arrays    [ val, val, ... ]
  - Single and double quoted strings
  - # comments
"""

from __future__ import annotations
from pathlib import Path
from typing import Any
import re


# ── Writer ────────────────────────────────────────────────────────────────────

def _encode(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return repr(v)
    if isinstance(v, str):
        esc = v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        return f'"{esc}"'
    if isinstance(v, list):
        if not v:
            return "[]"
        if all(isinstance(i, dict) for i in v):
            rows = ", ".join(
                "{" + ", ".join(f"{k} = {_encode(vv)}" for k, vv in i.items()) + "}"
                for i in v
            )
            return f"[{rows}]"
        return "[" + ", ".join(_encode(i) for i in v) + "]"
    if isinstance(v, dict):
        if not v:
            return "{}"
        pairs = ", ".join(f"{k} = {_encode(vv)}" for k, vv in v.items())
        return "{" + pairs + "}"
    return f'"{v}"'


def dump(path: Path, data: dict) -> None:
    """Write a structured dict to a TOML file.

    Top-level lists of dicts are written as [[section]] array-of-tables so the
    output is human-readable (one field per line).  All other values — scalars,
    inline tables, mixed lists — are written as regular key = value pairs.

    Any leading comment block from the existing file is preserved so that
    human-authored file headers are not lost on save.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    # ── Preserve leading file comments ────────────────────────────────────────
    header_lines: list[str] = []
    if path.exists():
        for raw in path.read_text(encoding="utf-8").splitlines():
            stripped = raw.strip()
            if stripped.startswith("#") or stripped == "":
                header_lines.append(raw)
            else:
                break
        # Drop trailing blank lines from the header; we'll add our own separator
        while header_lines and header_lines[-1].strip() == "":
            header_lines.pop()

    # ── Build output ──────────────────────────────────────────────────────────
    out: list[str] = []
    if header_lines:
        out.extend(header_lines)
        out.append("")   # blank separator between header and data

    # Categorise top-level keys by value type:
    #   scalar_keys  → written as  key = value  (strings, ints, lists of scalars, …)
    #   table_keys   → written as  [key] / key = val / …  (plain dict = sub-table)
    #   aot_keys     → written as  [[key]] / …            (list of dicts)
    scalar_keys: list[str] = []
    table_keys:  list[str] = []
    aot_keys:    list[str] = []
    for k, v in data.items():
        if isinstance(v, dict):
            table_keys.append(k)
        elif isinstance(v, list) and v and all(isinstance(i, dict) for i in v):
            aot_keys.append(k)
        else:
            scalar_keys.append(k)

    # Write scalars first
    for k in scalar_keys:
        out.append(f"{k} = {_encode(data[k])}")

    if scalar_keys and (table_keys or aot_keys):
        out.append("")   # blank line before first section

    # Write [table] sections
    for k in table_keys:
        out.append(f"[{k}]")
        for fk, fv in data[k].items():
            out.append(f"{fk} = {_encode(fv)}")
        out.append("")   # blank line after section

    # Write [[array-of-tables]] sections
    for k in aot_keys:
        for obj in data[k]:
            # Strip internal WCT metadata keys (prefixed with _)
            entries = {ek: ev for ek, ev in obj.items() if not ek.startswith("_")}
            out.append(f"[[{k}]]")
            for fk, fv in entries.items():
                out.append(f"{fk} = {_encode(fv)}")
            out.append("")   # blank line between records

    # Remove any trailing blank lines then add a final newline
    while out and out[-1] == "":
        out.pop()
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


# ── Reader helpers ────────────────────────────────────────────────────────────

def _strip_comment(line: str) -> str:
    """Remove trailing # comment, respecting quoted strings."""
    in_str = False
    str_char = ""
    i = 0
    while i < len(line):
        c = line[i]
        if in_str:
            if c == "\\" and i + 1 < len(line):
                i += 2
                continue
            if c == str_char:
                in_str = False
        elif c in ('"', "'"):
            in_str = True
            str_char = c
        elif c == "#":
            return line[:i].rstrip()
        i += 1
    return line


def _split_top_level(s: str) -> list[str]:
    """Split by commas not inside brackets, braces, or quotes."""
    parts: list[str] = []
    depth = 0
    in_str = False
    str_char = ""
    current: list[str] = []
    i = 0
    while i < len(s):
        c = s[i]
        if in_str:
            current.append(c)
            if c == "\\" and i + 1 < len(s):
                current.append(s[i + 1])
                i += 2
                continue
            if c == str_char:
                in_str = False
        elif c in ('"', "'"):
            in_str = True
            str_char = c
            current.append(c)
        elif c in ("[", "{"):
            depth += 1
            current.append(c)
        elif c in ("]", "}"):
            depth -= 1
            current.append(c)
        elif c == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
            i += 1
            continue
        else:
            current.append(c)
        i += 1
    if current:
        parts.append("".join(current).strip())
    return [p for p in parts if p]


def _parse_value(s: str) -> Any:
    s = s.strip()
    if s == "true":  return True
    if s == "false": return False
    try:
        if "." in s or ("e" in s.lower() and not s.startswith('"')):
            return float(s)
        return int(s)
    except ValueError:
        pass
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        inner = s[1:-1]
        return (inner
                .replace('\\"', '"')
                .replace("\\n", "\n")
                .replace("\\t", "\t")
                .replace("\\\\", "\\"))
    if len(s) >= 2 and s[0] == "'" and s[-1] == "'":
        return s[1:-1]
    if s.startswith("[") and s.endswith("]"):
        inner = s[1:-1].strip()
        if not inner:
            return []
        return [_parse_value(item) for item in _split_top_level(inner)]
    if s.startswith("{") and s.endswith("}"):
        inner = s[1:-1].strip()
        if not inner:
            return {}
        result: dict = {}
        for pair in _split_top_level(inner):
            k, _, v = pair.partition("=")
            result[k.strip()] = _parse_value(v.strip())
        return result
    return s


# ── Reader ────────────────────────────────────────────────────────────────────

def _open_brackets(s: str) -> int:
    """Count unclosed [ and { brackets in a string (respects strings)."""
    depth = 0
    in_str = False
    sc = ""
    for i, c in enumerate(s):
        if in_str:
            if c == "\\" :
                continue
            if c == sc:
                in_str = False
        elif c in ('"', "'"):
            in_str, sc = True, c
        elif c in ("[", "{"):
            depth += 1
        elif c in ("]", "}"):
            depth -= 1
    return depth


def load(path: Path) -> dict:
    """Parse a TOML file and return a dict. Supports [[array of tables]],
    multiline values (arrays/inline-table arrays that span lines), and
    triple-quoted strings which are collapsed to a single line."""
    text = Path(path).read_text(encoding="utf-8")

    # Pre-process: collapse triple-quoted strings into a single-line "..." value.
    # TOML triple-quoted strings can span multiple lines; we join them with spaces.
    def _collapse_triple(src: str) -> str:
        out = []
        i = 0
        while i < len(src):
            if src[i:i+3] == '"""':
                # Find closing triple-quote
                end = src.find('"""', i + 3)
                if end == -1:
                    # Unclosed — leave as-is
                    out.append(src[i:])
                    break
                inner = src[i+3:end]
                # Collapse newlines and indent to single spaces
                collapsed = " ".join(inner.split())
                # Escape any bare double-quotes inside, then wrap in double-quotes
                collapsed = collapsed.replace('"', '\\"')
                out.append(f'"{collapsed}"')
                i = end + 3
            else:
                out.append(src[i])
                i += 1
        return "".join(out)

    text = _collapse_triple(text)

    result: dict[str, Any] = {}
    current_obj: dict | None = None

    # Merge continuation lines so multiline values become one logical line
    logical_lines: list[str] = []
    pending = ""
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        # Skip pure-comment or blank lines only when not in a continuation
        if not pending and (not stripped or stripped.startswith("#")):
            continue
        if pending:
            # Strip inline comments from continuation lines carefully
            stripped = _strip_comment(stripped)
            pending += " " + stripped
        else:
            stripped = _strip_comment(stripped)
            if not stripped:
                continue
            pending = stripped
        # If brackets are balanced, flush to logical_lines
        if _open_brackets(pending) <= 0:
            logical_lines.append(pending)
            pending = ""

    if pending:   # unterminated — flush anyway
        logical_lines.append(pending)

    for line in logical_lines:
        # Array of tables:  [[key]]
        m = re.fullmatch(r'\[\[([^\]]+)\]\]', line)
        if m:
            key = m.group(1).strip()
            new_obj: dict = {}
            result.setdefault(key, []).append(new_obj)
            current_obj = new_obj
            continue

        # Plain table header:  [key]  or  [key.subkey]
        # Navigate to (or create) the named subtable and make it the current context.
        m = re.fullmatch(r'\[([^\]]+)\]', line)
        if m:
            key_path = m.group(1).strip()
            target = result
            for part in key_path.split("."):
                part = part.strip()
                if part not in target or not isinstance(target[part], dict):
                    target[part] = {}
                target = target[part]
            current_obj = target
            continue

        # Key = value
        if "=" in line:
            k, _, v_str = line.partition("=")
            k = k.strip()
            parsed = _parse_value(v_str.strip())
            if current_obj is not None:
                current_obj[k] = parsed
            else:
                result[k] = parsed

    return result




