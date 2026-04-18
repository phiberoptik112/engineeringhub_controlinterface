"""Parse Emacs config.el to extract org-roam-capture-templates and org-capture-templates.

This is a *limited* elisp reader — not a full interpreter. It handles the
sexp structures actually produced by Doom Emacs configs:

- Quoted lists: ``'((...) (...))``
- Backquoted templates with comma-unquote: `` `("key" ...) ``
- String literals (with escape sequences)
- Symbols, keywords (``:target``, ``t``, ``nil``), numbers
- ``setq`` / ``add-to-list`` forms
- ``%(...)`` elisp expressions preserved as opaque strings
- ``,(lambda ...)`` forms preserved as raw strings
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from engineering_hub.capture.models import (
    CaptureTemplate,
    HeadingSpec,
    TemplateType,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sexp tokenizer
# ---------------------------------------------------------------------------

@dataclass
class Token:
    kind: str  # "lparen", "rparen", "string", "symbol", "quote", "backquote", "comma", "number"
    value: str
    pos: int = 0


_TOKEN_RE = re.compile(
    r"""
    (?P<comment>;[^\n]*)          |  # line comment
    (?P<string>"(?:[^"\\]|\\.)*") |  # double-quoted string
    (?P<lparen>\()                |
    (?P<rparen>\))                |
    (?P<backquote>`)              |
    (?P<comma_at>,@)              |
    (?P<comma>,)                  |
    (?P<quote>')                  |
    (?P<number>-?\d+(?:\.\d+)?)  |
    (?P<symbol>[^\s()"',`;]+)    |
    (?P<ws>\s+)
    """,
    re.VERBOSE,
)


def tokenize(text: str) -> list[Token]:
    tokens: list[Token] = []
    for m in _TOKEN_RE.finditer(text):
        if m.lastgroup in ("comment", "ws"):
            continue
        kind = m.lastgroup or "symbol"
        if kind == "comma_at":
            kind = "comma"
        tokens.append(Token(kind=kind, value=m.group(), pos=m.start()))
    return tokens


# ---------------------------------------------------------------------------
# Sexp reader → nested Python lists / strings
# ---------------------------------------------------------------------------

class _Reader:
    """Recursive-descent reader that turns tokens into nested Python objects."""

    def __init__(self, tokens: list[Token]) -> None:
        self._tokens = tokens
        self._pos = 0

    def _peek(self) -> Token | None:
        return self._tokens[self._pos] if self._pos < len(self._tokens) else None

    def _advance(self) -> Token:
        tok = self._tokens[self._pos]
        self._pos += 1
        return tok

    def read(self) -> object:
        tok = self._peek()
        if tok is None:
            return None
        if tok.kind == "lparen":
            return self._read_list()
        if tok.kind == "quote":
            self._advance()
            inner = self.read()
            return ["quote", inner]
        if tok.kind == "backquote":
            self._advance()
            inner = self.read()
            return ["backquote", inner]
        if tok.kind == "comma":
            self._advance()
            inner = self.read()
            return ["unquote", inner]
        if tok.kind == "string":
            self._advance()
            return _unescape_string(tok.value)
        if tok.kind == "number":
            self._advance()
            return tok.value
        # symbol / keyword
        self._advance()
        return tok.value

    def _read_list(self) -> list:
        self._advance()  # consume (
        items: list = []
        while True:
            tok = self._peek()
            if tok is None:
                break
            if tok.kind == "rparen":
                self._advance()
                break
            items.append(self.read())
        return items

    def read_all(self) -> list:
        forms: list = []
        while self._pos < len(self._tokens):
            forms.append(self.read())
        return forms


def _unescape_string(s: str) -> str:
    """Remove surrounding quotes and process common escape sequences."""
    inner = s[1:-1]
    inner = inner.replace('\\"', '"')
    inner = inner.replace("\\n", "\n")
    inner = inner.replace("\\t", "\t")
    inner = inner.replace("\\\\", "\\")
    return inner


def parse_sexps(text: str) -> list:
    """Parse an elisp source string into nested Python lists."""
    return _Reader(tokenize(text)).read_all()


# ---------------------------------------------------------------------------
# Extract capture templates from parsed forms
# ---------------------------------------------------------------------------

def _find_setq_value(forms: list, var_name: str) -> object | None:
    """Find ``(setq var_name VALUE)`` and return VALUE."""
    for form in forms:
        if not isinstance(form, list) or len(form) < 3:
            continue
        if form[0] == "setq" and form[1] == var_name:
            val = form[2]
            if isinstance(val, list) and len(val) == 2 and val[0] == "quote":
                return val[1]
            return val
        result = _find_setq_value(form, var_name)
        if result is not None:
            return result
    return None


def _find_add_to_list_calls(forms: list, var_name: str) -> list:
    """Find all ``(add-to-list 'var_name TEMPLATE t)`` forms and return the template sexps."""
    results: list = []
    for form in forms:
        if not isinstance(form, list):
            continue
        if (
            len(form) >= 3
            and form[0] == "add-to-list"
            and isinstance(form[1], list)
            and len(form[1]) == 2
            and form[1][0] == "quote"
            and form[1][1] == var_name
        ):
            tpl = form[2]
            if isinstance(tpl, list) and len(tpl) == 2 and tpl[0] == "backquote":
                results.append(tpl[1])
            elif isinstance(tpl, list) and len(tpl) == 2 and tpl[0] == "quote":
                results.append(tpl[1])
            else:
                results.append(tpl)
        # Recurse into wrapper forms like (after! org ...)
        for sub in form:
            if isinstance(sub, list):
                results.extend(_find_add_to_list_calls([sub], var_name))
    return results


def _sexp_to_str(obj: object) -> str:
    """Flatten a parsed sexp back to a readable string (best-effort)."""
    if isinstance(obj, str):
        return obj
    if isinstance(obj, list):
        inner = " ".join(_sexp_to_str(x) for x in obj)
        return f"({inner})"
    return str(obj)


# ---------------------------------------------------------------------------
# Convert parsed template sexps → CaptureTemplate
# ---------------------------------------------------------------------------

def _extract_target_info(sexp: list) -> tuple[str, str, str]:
    """Extract (target_dir, filename_pattern, header_text) from :target sexp.

    Handles ``(file+head "path" "header")`` and ``(file "path")``.
    """
    target_dir = ""
    filename = ""
    header = ""

    if not isinstance(sexp, list) or len(sexp) < 2:
        return target_dir, filename, header

    target_type = sexp[0]
    if target_type in ("file+head", "file+headline"):
        path_str = sexp[1] if isinstance(sexp[1], str) else _sexp_to_str(sexp[1])
        header = sexp[2] if len(sexp) > 2 and isinstance(sexp[2], str) else ""
        parts = path_str.rsplit("/", 1)
        if len(parts) == 2:
            target_dir = parts[0] + "/"
            filename = parts[1]
        else:
            filename = path_str
    elif target_type == "file":
        path_str = sexp[1] if isinstance(sexp[1], str) else _sexp_to_str(sexp[1])
        parts = path_str.rsplit("/", 1)
        if len(parts) == 2:
            target_dir = parts[0] + "/"
            filename = parts[1]
        else:
            filename = path_str
    elif target_type == "file+function":
        # Agent dispatch template targeting a function
        filename = ""
        target_dir = ""

    return target_dir, filename, header


def _parse_header_into_structure(header: str) -> tuple[str, list[str], list[HeadingSpec]]:
    """Parse an org header string like ``#+title: ${title}\\n#+filetags: :a:b:\\n* Heading``."""
    title = "${title}"
    filetags: list[str] = []
    headings: list[HeadingSpec] = []

    for line in header.split("\n"):
        line_s = line.strip()
        if line_s.lower().startswith("#+title:"):
            title = line_s[len("#+title:"):].strip()
        elif line_s.lower().startswith("#+filetags:"):
            tags_raw = line_s[len("#+filetags:"):].strip()
            filetags = [t for t in tags_raw.strip(":").split(":") if t]
        elif line_s.startswith("*"):
            stars = 0
            for ch in line_s:
                if ch == "*":
                    stars += 1
                else:
                    break
            heading_title = line_s[stars:].strip()
            if heading_title:
                headings.append(HeadingSpec(title=heading_title, level=stars))

    return title, filetags, headings


def _roam_template_to_capture(sexp: list, index: int) -> CaptureTemplate | None:
    """Convert an org-roam-capture-templates entry to a CaptureTemplate."""
    if not isinstance(sexp, list) or len(sexp) < 4:
        return None

    key = sexp[0] if isinstance(sexp[0], str) else str(sexp[0])
    display_name = sexp[1] if isinstance(sexp[1], str) else str(sexp[1])
    # sexp[2] = type (usually "plain")
    body = sexp[3] if isinstance(sexp[3], str) else ""

    target_dir = ""
    filename = ""
    title = "${title}"
    filetags: list[str] = []
    headings: list[HeadingSpec] = []
    extras: dict = {}

    i = 4
    while i < len(sexp):
        item = sexp[i]
        if item == ":target" and i + 1 < len(sexp):
            target_info = sexp[i + 1]
            target_dir, filename, header = _extract_target_info(target_info)
            if header:
                title, filetags, headings = _parse_header_into_structure(header)
            i += 2
        elif item == ":unnarrowed":
            extras["unnarrowed"] = True if (i + 1 < len(sexp) and sexp[i + 1] == "t") else False
            i += 2
        elif item == ":empty-lines":
            extras["empty-lines"] = sexp[i + 1] if i + 1 < len(sexp) else 0
            i += 2
        elif item == ":immediate-finish":
            extras["immediate-finish"] = True if (i + 1 < len(sexp) and sexp[i + 1] != "nil") else False
            i += 2
        elif isinstance(item, str) and item.startswith(":"):
            extras[item.lstrip(":")] = sexp[i + 1] if i + 1 < len(sexp) else None
            i += 2
        else:
            i += 1

    name = key.lower().replace(" ", "-") or f"roam-template-{index}"

    return CaptureTemplate(
        name=name,
        display_name=display_name,
        key=key,
        description=f"Imported org-roam-capture template: {display_name}",
        template_type=TemplateType.ROAM_CAPTURE,
        target_dir=target_dir,
        filename_pattern=filename,
        title_pattern=title,
        filetags=filetags,
        headings=headings,
        raw_body=body if body != "%?" else "",
        elisp_extras=extras,
        source="emacs",
    )


def _capture_template_to_capture(sexp: list, index: int) -> CaptureTemplate | None:
    """Convert an org-capture-templates entry (from add-to-list) to CaptureTemplate."""
    if not isinstance(sexp, list) or len(sexp) < 4:
        return None

    key = sexp[0] if isinstance(sexp[0], str) else str(sexp[0])
    display_name = sexp[1] if isinstance(sexp[1], str) else str(sexp[1])
    # sexp[2] = type (usually "plain")
    body = ""

    # The body/target may be at different positions depending on the form.
    # For agent dispatch templates: (key desc type (file+function ...) body ...)
    target_sexp = None
    extras: dict = {}

    i = 3
    while i < len(sexp):
        item = sexp[i]
        if isinstance(item, list) and item and isinstance(item[0], str) and item[0].startswith("file+"):
            target_sexp = item
            i += 1
        elif isinstance(item, str) and not item.startswith(":"):
            body = item
            i += 1
        elif item == ":empty-lines" and i + 1 < len(sexp):
            extras["empty-lines"] = sexp[i + 1]
            i += 2
        elif item == ":immediate-finish" and i + 1 < len(sexp):
            val = sexp[i + 1]
            extras["immediate-finish"] = val != "nil"
            i += 2
        elif isinstance(item, str) and item.startswith(":"):
            extras[item.lstrip(":")] = sexp[i + 1] if i + 1 < len(sexp) else None
            i += 2
        else:
            i += 1

    # Detect agent dispatch from body pattern
    agent_type = ""
    body_str = body if isinstance(body, str) else _sexp_to_str(body)
    agent_match = re.search(r"@([\w-]+):", body_str)
    if agent_match:
        agent_type = agent_match.group(1)

    name = key.lower().replace(" ", "-") or f"org-capture-{index}"

    return CaptureTemplate(
        name=name,
        display_name=display_name,
        key=key,
        description=f"Imported org-capture template: {display_name}",
        template_type=TemplateType.ORG_CAPTURE,
        raw_body=body_str,
        elisp_extras=extras,
        source="emacs",
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_emacs_config(config_path: Path) -> list[CaptureTemplate]:
    """Parse an Emacs config.el and return all capture templates found.

    Extracts both ``org-roam-capture-templates`` (setq) and
    ``org-capture-templates`` (add-to-list) definitions.
    """
    if not config_path.exists():
        logger.warning("Emacs config not found: %s", config_path)
        return []

    text = config_path.read_text(encoding="utf-8", errors="replace")
    forms = parse_sexps(text)
    templates: list[CaptureTemplate] = []

    # 1. org-roam-capture-templates from (setq org-roam-capture-templates '(...))
    roam_templates = _find_setq_value(forms, "org-roam-capture-templates")
    if isinstance(roam_templates, list):
        for idx, entry in enumerate(roam_templates):
            if isinstance(entry, list):
                tpl = _roam_template_to_capture(entry, idx)
                if tpl:
                    templates.append(tpl)

    # 2. org-capture-templates from (add-to-list 'org-capture-templates ...)
    capture_sexps = _find_add_to_list_calls(forms, "org-capture-templates")
    for idx, entry in enumerate(capture_sexps):
        tpl = _capture_template_to_capture(entry, idx)
        if tpl:
            templates.append(tpl)

    logger.info(
        "Parsed %d template(s) from %s (%d roam-capture, %d org-capture)",
        len(templates),
        config_path.name,
        sum(1 for t in templates if t.template_type == TemplateType.ROAM_CAPTURE),
        sum(1 for t in templates if t.template_type == TemplateType.ORG_CAPTURE),
    )
    return templates
