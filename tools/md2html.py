#!/usr/bin/env python3
"""
tools/md2html.py — Convert a Markdown file to a self-contained HTML page.

Usage:
  python tools/md2html.py <input.md>              # writes <input>.html
  python tools/md2html.py <input.md> <output.html>
  python tools/md2html.py <input.md> --stdout     # print to stdout

Handles: headings, bold/italic, inline code, fenced code blocks,
         ordered/unordered lists, blockquotes, tables, horizontal rules,
         links, images, and paragraphs.  No external dependencies.
"""

import re
import sys
import html as html_module
from pathlib import Path


# ── Inline formatting ─────────────────────────────────────────────────────────

def _inline(text: str) -> str:
    """Apply inline Markdown formatting to a text fragment."""
    # Protect existing HTML entities
    text = html_module.escape(text, quote=False)

    # Inline code (do first so contents are not further processed)
    parts = re.split(r'(`+)', text)
    out = []
    i = 0
    while i < len(parts):
        if re.fullmatch(r'`+', parts[i]):
            fence = parts[i]
            j = i + 1
            while j < len(parts) and parts[j] != fence:
                j += 1
            if j < len(parts):
                code_content = "".join(parts[i+1:j])
                out.append(f"<code>{code_content}</code>")
                i = j + 1
            else:
                out.append(fence)
                i += 1
        else:
            out.append(parts[i])
            i += 1
    text = "".join(out)

    # Bold+italic
    text = re.sub(r'\*\*\*(.+?)\*\*\*', r'<strong><em>\1</em></strong>', text)
    text = re.sub(r'___(.+?)___',       r'<strong><em>\1</em></strong>', text)
    # Bold
    text = re.sub(r'\*\*(.+?)\*\*',    r'<strong>\1</strong>', text)
    text = re.sub(r'__(.+?)__',         r'<strong>\1</strong>', text)
    # Italic
    text = re.sub(r'\*(.+?)\*',         r'<em>\1</em>', text)
    text = re.sub(r'_(.+?)_',           r'<em>\1</em>', text)
    # Strikethrough
    text = re.sub(r'~~(.+?)~~',         r'<s>\1</s>', text)
    # Images (before links)
    text = re.sub(r'!\[([^\]]*)\]\(([^)]+)\)',
                  lambda m: f'<img src="{m.group(2)}" alt="{m.group(1)}">', text)
    # Links
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)',
                  lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>', text)

    return text


# ── Block parser ──────────────────────────────────────────────────────────────

def _parse_table(lines: list[str]) -> str:
    rows = []
    for line in lines:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        rows.append(cells)
    if not rows:
        return ""
    html = ["<table>", "<thead>", "<tr>"]
    for cell in rows[0]:
        html.append(f"  <th>{_inline(cell)}</th>")
    html += ["</tr>", "</thead>", "<tbody>"]
    for row in rows[2:]:
        html.append("<tr>")
        for cell in row:
            html.append(f"  <td>{_inline(cell)}</td>")
        html.append("</tr>")
    html += ["</tbody>", "</table>"]
    return "\n".join(html)


def _is_separator_row(line: str) -> bool:
    return bool(re.fullmatch(r'[\s|:\-]+', line.strip()))


def _flush_paragraph(buf: list[str]) -> str:
    if not buf:
        return ""
    text = " ".join(buf).strip()
    buf.clear()
    return f"<p>{_inline(text)}</p>\n" if text else ""


def convert(md: str) -> str:
    lines = md.splitlines()
    html_parts: list[str] = []
    para_buf: list[str] = []

    # List state
    ul_open = False
    ol_open = False

    def close_lists():
        nonlocal ul_open, ol_open
        if ul_open:
            html_parts.append("</ul>")
            ul_open = False
        if ol_open:
            html_parts.append("</ol>")
            ol_open = False

    def flush_para():
        p = _flush_paragraph(para_buf)
        if p:
            html_parts.append(p)

    i = 0
    while i < len(lines):
        line = lines[i]

        # ── Fenced code block ────────────────────────────────────────────────
        if re.match(r'^```', line):
            flush_para()
            close_lists()
            lang = line[3:].strip()
            lang_attr = f' class="language-{lang}"' if lang else ""
            code_lines = []
            i += 1
            while i < len(lines) and not re.match(r'^```', lines[i]):
                code_lines.append(html_module.escape(lines[i]))
                i += 1
            code = "\n".join(code_lines)
            html_parts.append(f"<pre><code{lang_attr}>{code}</code></pre>")
            i += 1
            continue

        # ── Heading ──────────────────────────────────────────────────────────
        m = re.match(r'^(#{1,6})\s+(.*)', line)
        if m:
            flush_para()
            close_lists()
            level = len(m.group(1))
            text  = _inline(m.group(2))
            # Make a simple anchor from the heading text
            anchor = re.sub(r'[^a-z0-9]+', '-', m.group(2).lower()).strip('-')
            html_parts.append(f'<h{level} id="{anchor}">{text}</h{level}>')
            i += 1
            continue

        # ── Horizontal rule ───────────────────────────────────────────────────
        if re.match(r'^(\*{3,}|-{3,}|_{3,})\s*$', line):
            flush_para()
            close_lists()
            html_parts.append("<hr>")
            i += 1
            continue

        # ── Blockquote ────────────────────────────────────────────────────────
        if line.startswith(">"):
            flush_para()
            close_lists()
            bq_lines = []
            while i < len(lines) and lines[i].startswith(">"):
                bq_lines.append(lines[i].lstrip(">").lstrip(" "))
                i += 1
            inner = convert("\n".join(bq_lines))
            html_parts.append(f"<blockquote>\n{inner}\n</blockquote>")
            continue

        # ── Table ─────────────────────────────────────────────────────────────
        if "|" in line and i + 1 < len(lines) and _is_separator_row(lines[i + 1]):
            flush_para()
            close_lists()
            table_lines = []
            while i < len(lines) and "|" in lines[i]:
                table_lines.append(lines[i])
                i += 1
            html_parts.append(_parse_table(table_lines))
            continue

        # ── Unordered list ────────────────────────────────────────────────────
        m = re.match(r'^(\s*)([-*+])\s+(.*)', line)
        if m:
            flush_para()
            if ol_open:
                html_parts.append("</ol>")
                ol_open = False
            if not ul_open:
                html_parts.append("<ul>")
                ul_open = True
            html_parts.append(f"  <li>{_inline(m.group(3))}</li>")
            i += 1
            continue

        # ── Ordered list ──────────────────────────────────────────────────────
        m = re.match(r'^\s*\d+\.\s+(.*)', line)
        if m:
            flush_para()
            if ul_open:
                html_parts.append("</ul>")
                ul_open = False
            if not ol_open:
                html_parts.append("<ol>")
                ol_open = True
            html_parts.append(f"  <li>{_inline(m.group(1))}</li>")
            i += 1
            continue

        # ── Blank line ────────────────────────────────────────────────────────
        if not line.strip():
            flush_para()
            close_lists()
            i += 1
            continue

        # ── Default: paragraph text ───────────────────────────────────────────
        close_lists()
        para_buf.append(line)
        i += 1

    flush_para()
    close_lists()
    return "\n".join(html_parts)


# ── Page template ─────────────────────────────────────────────────────────────

_CSS = """\
body {
  font-family: Georgia, 'Times New Roman', serif;
  max-width: 860px;
  margin: 2rem auto;
  padding: 0 1.5rem;
  line-height: 1.7;
  color: #222;
  background: #fafaf8;
}
h1, h2, h3, h4, h5, h6 {
  font-family: 'Palatino Linotype', Palatino, serif;
  margin-top: 2rem;
  border-bottom: 1px solid #ddd;
  padding-bottom: .25rem;
}
h1 { font-size: 2em; }
h2 { font-size: 1.5em; }
h3 { font-size: 1.2em; border-bottom: none; }
code {
  background: #eee;
  padding: .1em .35em;
  border-radius: 3px;
  font-size: .9em;
}
pre {
  background: #1e1e1e;
  color: #d4d4d4;
  padding: 1rem 1.2rem;
  border-radius: 6px;
  overflow-x: auto;
}
pre code {
  background: none;
  padding: 0;
  font-size: .88em;
  color: inherit;
}
blockquote {
  border-left: 4px solid #aaa;
  margin: 1rem 0;
  padding: .5rem 1rem;
  color: #555;
  background: #f3f3f0;
}
table {
  border-collapse: collapse;
  width: 100%;
  margin: 1rem 0;
}
th, td {
  border: 1px solid #ccc;
  padding: .45rem .75rem;
  text-align: left;
}
th { background: #eee; font-weight: bold; }
tr:nth-child(even) { background: #f7f7f5; }
hr { border: none; border-top: 1px solid #ccc; margin: 2rem 0; }
img { max-width: 100%; }
a { color: #3a6fa0; }
"""


def wrap_html(body: str, title: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html_module.escape(title)}</title>
<style>
{_CSS}
</style>
</head>
<body>
{body}
</body>
</html>
"""


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return

    input_path = Path(args[0])
    if not input_path.exists():
        print(f"Error: file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    stdout = "--stdout" in args
    output_path = None
    for a in args[1:]:
        if not a.startswith("-"):
            output_path = Path(a)
            break
    if not stdout and output_path is None:
        output_path = input_path.with_suffix(".html")

    md_text  = input_path.read_text(encoding="utf-8")
    body     = convert(md_text)
    title    = input_path.stem.replace("_", " ").replace("-", " ").title()
    full_html = wrap_html(body, title)

    if stdout:
        print(full_html)
    else:
        output_path.write_text(full_html, encoding="utf-8")
        print(f"Written: {output_path}")


if __name__ == "__main__":
    main()
