#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import re
import textwrap
from html.parser import HTMLParser
from pathlib import Path


BLOCK_TAGS = {
    "p",
    "div",
    "section",
    "article",
    "body",
    "table",
    "tr",
    "blockquote",
}
HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
LIST_CONTAINER_TAGS = {"ul", "ol"}
LIST_ITEM_TAGS = {"li"}
PREFORMATTED_TAGS = {"pre", "code"}
SKIP_TAGS = {"script", "style"}
DEFAULT_WRAP_WIDTH = 72


def sniff_html_encoding(data: bytes) -> str:
    head = data[:4096].decode("ascii", errors="ignore")
    match = re.search(r"charset\s*=\s*([A-Za-z0-9._-]+)", head, re.IGNORECASE)
    if match:
        return match.group(1)
    return "utf-8"


class RFCTextHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._skip_depth = 0
        self._in_title = False
        self._in_pre = False
        self._in_heading = False
        self._list_depth = 0
        self._current_prefix = ""
        self._current_parts: list[str] = []
        self._current_kind = "paragraph"
        self.blocks: list[tuple[str, str, str]] = []

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        tag = tag.lower()
        if tag in SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return

        if tag in BLOCK_TAGS:
            self._flush()
            return
        if tag in HEADING_TAGS:
            self._flush()
            self._in_heading = True
            self._current_kind = "heading"
            return
        if tag in LIST_CONTAINER_TAGS:
            self._flush()
            self._list_depth += 1
            return
        if tag in LIST_ITEM_TAGS:
            self._flush()
            indent = "  " * max(self._list_depth - 1, 0)
            self._current_prefix = f"{indent}- "
            self._current_kind = "list_item"
            return
        if tag in PREFORMATTED_TAGS:
            self._flush()
            self._in_pre = True
            self._current_kind = "pre"
            return
        if tag == "br":
            if self._in_pre:
                self._current_parts.append("\n")
            else:
                self._append_text("\n")
            return
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in SKIP_TAGS:
            if self._skip_depth:
                self._skip_depth -= 1
            return
        if self._skip_depth:
            return

        if tag == "title":
            self._in_title = False
            return
        if tag in HEADING_TAGS:
            self._flush()
            self._in_heading = False
            return
        if tag in LIST_ITEM_TAGS:
            self._flush()
            self._current_prefix = ""
            self._current_kind = "paragraph"
            return
        if tag in LIST_CONTAINER_TAGS:
            self._flush()
            self._list_depth = max(self._list_depth - 1, 0)
            return
        if tag in PREFORMATTED_TAGS:
            self._flush()
            self._in_pre = False
            self._current_kind = "paragraph"
            return
        if tag in BLOCK_TAGS:
            self._flush()

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if not data:
            return

        text = html.unescape(data)
        if self._in_title:
            self.title += text.strip()
        if self._in_pre:
            self._current_parts.append(text)
            return
        self._append_text(text)

    def _append_text(self, text: str) -> None:
        cleaned = text.replace("\r\n", "\n").replace("\r", "\n")
        if "\n" in cleaned:
            pieces = cleaned.split("\n")
            for idx, piece in enumerate(pieces):
                piece = re.sub(r"\s+", " ", piece).strip()
                if piece:
                    self._current_parts.append(piece)
                if idx != len(pieces) - 1 and self._current_parts:
                    self._current_parts.append("\n")
        else:
            piece = re.sub(r"\s+", " ", cleaned).strip()
            if piece:
                self._current_parts.append(piece)

    def _flush(self) -> None:
        if not self._current_parts:
            return

        if self._current_kind == "pre":
            text = "".join(self._current_parts)
            text = text.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
        else:
            text = " ".join(part for part in self._current_parts if part != "\n")
            text = re.sub(r"\s+", " ", text).strip()

        if text:
            self.blocks.append((self._current_kind, self._current_prefix, text))
        self._current_parts = []
        if self._current_kind not in {"heading", "pre"}:
            self._current_prefix = ""
        self._current_kind = "paragraph"

    def finalize(self) -> list[tuple[str, str, str]]:
        self._flush()
        return self.blocks


def render_blocks(blocks: list[tuple[str, str, str]], title: str, width: int = DEFAULT_WRAP_WIDTH) -> str:
    out: list[str] = []
    title = title.strip()
    if title:
        out.append(title)
        out.append("=" * min(len(title), width))
        out.append("")

    for kind, prefix, text in blocks:
        if kind == "heading":
            out.append(text)
            out.append("")
            continue
        if kind == "pre":
            for line in text.splitlines():
                out.append(line.rstrip())
            out.append("")
            continue
        if kind == "list_item":
            wrapped = textwrap.fill(
                text,
                width=width,
                initial_indent=prefix,
                subsequent_indent=" " * len(prefix),
                break_long_words=False,
                break_on_hyphens=False,
            )
            out.append(wrapped)
            out.append("")
            continue

        wrapped = textwrap.fill(
            text,
            width=width,
            break_long_words=False,
            break_on_hyphens=False,
        )
        out.append(wrapped)
        out.append("")

    return "\n".join(out).rstrip() + "\n"


def convert_html_to_rfc_text(input_path: Path, output_path: Path, width: int = DEFAULT_WRAP_WIDTH) -> None:
    data = input_path.read_bytes()
    encoding = sniff_html_encoding(data)
    text = data.decode(encoding, errors="replace")

    parser = RFCTextHTMLParser()
    parser.feed(text)
    blocks = parser.finalize()
    rendered = render_blocks(blocks, parser.title, width=width)

    output_path.write_text(rendered, encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Convert HTML into RFC-style plain text")
    parser.add_argument("input_html", help="Input HTML file")
    parser.add_argument("-o", "--output", help="Output TXT file; defaults to same directory with .txt suffix")
    parser.add_argument("--width", type=int, default=DEFAULT_WRAP_WIDTH, help="Line wrap width (default: 72)")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    input_path = Path(args.input_html)
    if not input_path.exists():
        parser.error(f"Input file not found: {input_path}")

    output_path = Path(args.output) if args.output else input_path.with_suffix(".txt")
    convert_html_to_rfc_text(input_path, output_path, width=args.width)
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
