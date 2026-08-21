"""Safe, native-Tk transcript rendering for LLM conversations."""

from __future__ import annotations

import re
import tkinter as tk
from collections.abc import Sequence
from dataclasses import dataclass
from functools import partial
from tkinter import scrolledtext, ttk

from aiopenstudio.core.contracts import ConversationMessage, MessageRole, MessageStatus

_INERT_LINK = re.compile(r"\[([^\]]+)]\(([^)]+)\)")


@dataclass(frozen=True, slots=True)
class MarkdownBlock:
    kind: str
    text: str
    label: str = ""


def markdown_blocks(content: str, *, plain_text: bool = False) -> tuple[MarkdownBlock, ...]:
    """Parse a deliberately small Markdown subset without activating links or HTML."""
    if plain_text:
        return (MarkdownBlock("plain", content),)
    blocks: list[MarkdownBlock] = []
    paragraph: list[str] = []
    code: list[str] = []
    code_language = ""
    in_code = False

    def flush_paragraph() -> None:
        if paragraph:
            text = "\n".join(paragraph)
            blocks.append(MarkdownBlock("paragraph", _inert_links(text)))
            paragraph.clear()

    for line in content.splitlines():
        if line.startswith("```"):
            if in_code:
                blocks.append(MarkdownBlock("code", "\n".join(code), code_language))
                code.clear()
                in_code = False
            else:
                flush_paragraph()
                code_language = line[3:].strip()[:40]
                in_code = True
            continue
        if in_code:
            code.append(line)
            continue
        if not line.strip():
            flush_paragraph()
        elif line.startswith("#") and line.lstrip("#").startswith(" "):
            flush_paragraph()
            level = len(line) - len(line.lstrip("#"))
            blocks.append(
                MarkdownBlock(
                    f"heading{min(level, 3)}",
                    _inert_links(line[level:].strip()),
                )
            )
        elif line.startswith(("- ", "* ", "> ")):
            flush_paragraph()
            blocks.append(MarkdownBlock("list", _inert_links(line)))
        else:
            paragraph.append(line)
    if in_code:
        blocks.append(MarkdownBlock("code", "\n".join(code), code_language))
    flush_paragraph()
    return tuple(blocks)


def _inert_links(text: str) -> str:
    return _INERT_LINK.sub(lambda match: f"{match.group(1)} ⟨{match.group(2)}⟩", text)


class LLMTranscript(ttk.Frame):
    """Conversation renderer with inert links and per-block code copy buttons."""

    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self._text = scrolledtext.ScrolledText(
            self,
            wrap=tk.WORD,
            state=tk.DISABLED,
            borderwidth=0,
            padx=12,
            pady=10,
        )
        self._text.grid(row=0, column=0, sticky="nsew")
        self._text.tag_configure("role", font=("TkDefaultFont", 10, "bold"), spacing1=10)
        self._text.tag_configure("system", foreground="#725800")
        self._text.tag_configure("cancelled", foreground="#8a4b08")
        self._text.tag_configure("failed", foreground="#9d2222")
        self._text.tag_configure("thinking", foreground="#686868")
        self._text.tag_configure("heading1", font=("TkDefaultFont", 14, "bold"))
        self._text.tag_configure("heading2", font=("TkDefaultFont", 12, "bold"))
        self._text.tag_configure("heading3", font=("TkDefaultFont", 10, "bold"))
        self._text.tag_configure("code", font=("TkFixedFont", 10), lmargin1=16, lmargin2=16)
        self._text.tag_configure("list", lmargin1=12, lmargin2=24)

    def render(self, messages: Sequence[ConversationMessage], *, plain_text: bool) -> None:
        self._editable(True)
        self._text.delete("1.0", tk.END)
        for message in messages:
            self._render_message(message, plain_text=plain_text)
        self._editable(False)
        self._text.see(tk.END)

    def begin_stream(self, prompt: str, *, model_name: str) -> None:
        self._editable(True)
        self._text.insert(tk.END, "\nTú\n", "role")
        self._text.insert(tk.END, f"{prompt}\n")
        self._text.insert(tk.END, f"\nAsistente · {model_name}\n", "role")
        self._editable(False)
        self._text.see(tk.END)

    def append_response(self, text: str) -> None:
        self._append(text)

    def append_thinking(self, text: str, *, visible: bool) -> None:
        if visible:
            self._append(text, "thinking")

    def append_notice(self, text: str, *, error: bool = False) -> None:
        self._append(f"\n[{text}]\n", "failed" if error else "cancelled")

    def _render_message(self, message: ConversationMessage, *, plain_text: bool) -> None:
        labels = {
            MessageRole.SYSTEM: "Sistema",
            MessageRole.USER: "Tú",
            MessageRole.ASSISTANT: "Asistente",
            MessageRole.TOOL: "Herramienta",
        }
        detail = f" · {message.model_key}" if message.model_key else ""
        status = "" if message.status is MessageStatus.COMPLETE else f" · {message.status.value}"
        self._text.insert(tk.END, f"{labels[message.role]}{detail}{status}\n", "role")
        for block in markdown_blocks(message.content, plain_text=plain_text):
            if block.kind == "code":
                language = f"Código {block.label}" if block.label else "Código"
                self._text.insert(tk.END, f"{language}  ")
                button = ttk.Button(
                    self._text,
                    text="Copiar",
                    command=partial(self._copy, block.text),
                )
                self._text.window_create(tk.END, window=button)
                self._text.insert(tk.END, "\n")
                self._text.insert(tk.END, f"{block.text}\n", "code")
            else:
                tag = (
                    block.kind
                    if block.kind in {"heading1", "heading2", "heading3", "list"}
                    else ()
                )
                self._text.insert(tk.END, f"{block.text}\n", tag)
        if message.status is MessageStatus.CANCELLED:
            self._text.insert(
                tk.END,
                "[respuesta parcial cancelada; no se reinyecta]\n",
                "cancelled",
            )
        elif message.status is MessageStatus.FAILED:
            self._text.insert(tk.END, "[respuesta fallida; no se reinyecta]\n", "failed")
        self._text.insert(tk.END, "\n")

    def _copy(self, content: str) -> None:
        self.clipboard_clear()
        self.clipboard_append(content)

    def _append(self, content: str, tag: str | tuple[str, ...] = ()) -> None:
        self._editable(True)
        self._text.insert(tk.END, content, tag)
        self._editable(False)
        self._text.see(tk.END)

    def _editable(self, enabled: bool) -> None:
        self._text.configure(state=tk.NORMAL if enabled else tk.DISABLED)
