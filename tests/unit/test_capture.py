"""Tests for hub capture templates: models, YAML loader, elisp parser, generator, and applicator."""

from __future__ import annotations

import textwrap
from datetime import datetime
from pathlib import Path

import pytest
import yaml

from engineering_hub.capture.applicator import (
    _expand_date_patterns,
    _expand_placeholders,
    _slugify,
    apply_template,
)
from engineering_hub.capture.elisp_generator import (
    generate_elisp,
    generate_org_capture_sexp,
    generate_roam_template_sexp,
)
from engineering_hub.capture.elisp_parser import (
    parse_emacs_config,
    parse_sexps,
    tokenize,
)
from engineering_hub.capture.loader import load_capture_templates, save_capture_template
from engineering_hub.capture.models import (
    AgentDispatchSpec,
    CaptureTemplate,
    DispatchTrigger,
    FieldSpec,
    FieldType,
    HeadingSpec,
    TemplateType,
)


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------


class TestCaptureTemplateModel:
    def test_round_trip_dict(self):
        tpl = CaptureTemplate(
            name="test",
            display_name="Test Template",
            key="tt",
            description="A test template",
            template_type=TemplateType.ROAM_CAPTURE,
            target_dir="tests/",
            filename_pattern="%Y%m%d-${slug}.org",
            title_pattern="${title}",
            filetags=["test", "example"],
            properties={"CLIENT": "${client}"},
            headings=[HeadingSpec(title="Notes", level=1, body="")],
            fields=[FieldSpec(name="client", prompt="Client name", type=FieldType.TEXT)],
            agent_dispatch=AgentDispatchSpec(
                agent_type="research",
                description_template="Summarize for ${client}",
                on=DispatchTrigger.MANUAL,
            ),
        )

        d = tpl.to_dict()
        restored = CaptureTemplate.from_dict(d)

        assert restored.name == "test"
        assert restored.display_name == "Test Template"
        assert restored.key == "tt"
        assert restored.template_type == TemplateType.ROAM_CAPTURE
        assert restored.filetags == ["test", "example"]
        assert restored.properties == {"CLIENT": "${client}"}
        assert len(restored.headings) == 1
        assert restored.headings[0].title == "Notes"
        assert len(restored.fields) == 1
        assert restored.fields[0].name == "client"
        assert restored.agent_dispatch is not None
        assert restored.agent_dispatch.agent_type == "research"
        assert restored.agent_dispatch.on == DispatchTrigger.MANUAL

    def test_field_spec_choices(self):
        f = FieldSpec(
            name="priority",
            prompt="Priority level",
            type=FieldType.CHOICE,
            choices=["low", "medium", "high"],
            default="medium",
        )
        d = f.to_dict()
        assert d["choices"] == ["low", "medium", "high"]
        assert d["default"] == "medium"
        restored = FieldSpec.from_dict(d)
        assert restored.choices == ["low", "medium", "high"]

    def test_heading_spec_nested(self):
        h = HeadingSpec(
            title="Parent",
            level=1,
            children=[HeadingSpec(title="Child", level=2, body="body text")],
        )
        d = h.to_dict()
        assert len(d["children"]) == 1
        restored = HeadingSpec.from_dict(d)
        assert len(restored.children) == 1
        assert restored.children[0].title == "Child"
        assert restored.children[0].body == "body text"


# ---------------------------------------------------------------------------
# YAML loader tests
# ---------------------------------------------------------------------------


class TestYAMLLoader:
    def test_load_and_save(self, tmp_path: Path):
        tpl = CaptureTemplate(
            name="invoice",
            display_name="Invoice",
            key="iv",
            description="Create an invoice",
            filetags=["invoice"],
            fields=[FieldSpec(name="amount", prompt="Amount", type=FieldType.NUMBER)],
        )

        path = save_capture_template(tpl, tmp_path)
        assert path.exists()
        assert path.name == "invoice.yaml"

        loaded = load_capture_templates(tmp_path)
        assert "invoice" in loaded
        assert loaded["invoice"].display_name == "Invoice"
        assert loaded["invoice"].fields[0].name == "amount"
        assert loaded["invoice"].source == "yaml"

    def test_load_contracting_hours_example(self):
        """Load the actual example template from the repo."""
        here = Path(__file__).resolve().parent.parent.parent / "capture_templates"
        if not here.exists():
            pytest.skip("capture_templates/ directory not found")

        templates = load_capture_templates(here)
        assert "contracting-hours" in templates
        tpl = templates["contracting-hours"]
        assert tpl.key == "ch"
        assert "contracting" in tpl.filetags
        assert len(tpl.fields) == 5
        assert tpl.agent_dispatch is not None
        assert tpl.agent_dispatch.agent_type == "research"

    def test_load_empty_dir(self, tmp_path: Path):
        loaded = load_capture_templates(tmp_path)
        assert loaded == {}

    def test_load_nonexistent_dir(self, tmp_path: Path):
        loaded = load_capture_templates(tmp_path / "nonexistent")
        assert loaded == {}


# ---------------------------------------------------------------------------
# Elisp tokenizer / parser tests
# ---------------------------------------------------------------------------


class TestElispTokenizer:
    def test_basic_tokens(self):
        tokens = tokenize('(setq x "hello")')
        kinds = [t.kind for t in tokens]
        assert kinds == ["lparen", "symbol", "symbol", "string", "rparen"]

    def test_quoted_list(self):
        tokens = tokenize("'(a b c)")
        kinds = [t.kind for t in tokens]
        assert kinds == ["quote", "lparen", "symbol", "symbol", "symbol", "rparen"]

    def test_backquote_and_comma(self):
        tokens = tokenize('`("key" ,(lambda () (foo)))')
        kinds = [t.kind for t in tokens]
        assert "backquote" in kinds
        assert "comma" in kinds

    def test_comment_ignored(self):
        tokens = tokenize(';; comment\n(a b)')
        kinds = [t.kind for t in tokens]
        assert "comment" not in kinds
        assert kinds == ["lparen", "symbol", "symbol", "rparen"]


class TestElispParser:
    def test_parse_simple_setq(self):
        forms = parse_sexps('(setq x "hello")')
        assert len(forms) == 1
        assert forms[0][0] == "setq"
        assert forms[0][1] == "x"
        assert forms[0][2] == "hello"

    def test_parse_quoted_list(self):
        forms = parse_sexps("(setq my-list '((a b) (c d)))")
        assert len(forms) == 1
        form = forms[0]
        assert form[0] == "setq"
        val = form[2]
        assert val[0] == "quote"
        assert isinstance(val[1], list)

    def test_parse_roam_capture_template_snippet(self):
        elisp = textwrap.dedent("""\
        (setq org-roam-capture-templates
              '(("d" "default" plain
                 "%?"
                 :target (file+head "%<%Y%m%d>-${slug}.org"
                                    "#+title: ${title}\\n#+created: %U\\n")
                 :unnarrowed t)))
        """)
        forms = parse_sexps(elisp)
        assert len(forms) == 1

    def test_parse_add_to_list_snippet(self):
        elisp = textwrap.dedent("""\
        (add-to-list 'org-capture-templates
          `("Ar" "Research" plain
            (file+function
             ,(lambda () (eh/daily-journal-file))
             eh/ensure-overnight-heading)
            "- [ ] @research: %?"
            :empty-lines 0
            :immediate-finish nil)
          t)
        """)
        forms = parse_sexps(elisp)
        assert len(forms) == 1


class TestParseEmacsConfig:
    def test_parse_real_config(self):
        """Parse the actual user config.el if available."""
        config_path = Path.home() / ".doom.d" / "config.el"
        if not config_path.exists():
            pytest.skip("~/.doom.d/config.el not found")

        templates = parse_emacs_config(config_path)
        assert len(templates) > 0

        roam_templates = [t for t in templates if t.template_type == TemplateType.ROAM_CAPTURE]
        org_templates = [t for t in templates if t.template_type == TemplateType.ORG_CAPTURE]
        assert len(roam_templates) >= 1
        assert len(org_templates) >= 1

        # Check that 'd' (default) template was parsed
        keys = [t.key for t in roam_templates]
        assert "d" in keys

    def test_parse_synthetic_config(self, tmp_path: Path):
        config = tmp_path / "config.el"
        config.write_text(textwrap.dedent("""\
        (setq org-roam-capture-templates
              '(("d" "default note" plain
                 "%?"
                 :target (file+head "%<%Y%m%d>-${slug}.org"
                                    "#+title: ${title}\\n#+filetags: :note:\\n\\n* Notes\\n")
                 :unnarrowed t)

                ("p" "project" plain
                 "%?"
                 :target (file+head "projects/%<%Y%m%d>-${slug}.org"
                                    "#+title: ${title}\\n#+category: Project\\n")
                 :unnarrowed t)))

        (after! org
          (add-to-list 'org-capture-templates
            `("Ar" "Research agent" plain
              (file+function
               ,(lambda () (eh/daily-journal-file))
               eh/ensure-overnight-heading)
              "- [ ] @research: %?"
              :empty-lines 0
              :immediate-finish nil)
            t))
        """))

        templates = parse_emacs_config(config)
        assert len(templates) == 3

        roam = [t for t in templates if t.template_type == TemplateType.ROAM_CAPTURE]
        assert len(roam) == 2
        assert roam[0].key == "d"
        assert roam[0].display_name == "default note"
        assert "note" in roam[0].filetags

        assert roam[1].key == "p"
        assert roam[1].target_dir == "projects/"

        org = [t for t in templates if t.template_type == TemplateType.ORG_CAPTURE]
        assert len(org) == 1
        assert org[0].key == "Ar"

    def test_parse_nonexistent_config(self, tmp_path: Path):
        templates = parse_emacs_config(tmp_path / "missing.el")
        assert templates == []


# ---------------------------------------------------------------------------
# Elisp generator tests
# ---------------------------------------------------------------------------


class TestElispGenerator:
    def test_generate_roam_template(self):
        tpl = CaptureTemplate(
            name="test",
            display_name="Test Note",
            key="tn",
            description="Test",
            template_type=TemplateType.ROAM_CAPTURE,
            target_dir="tests/",
            filename_pattern="%Y%m%d-${slug}.org",
            title_pattern="${title}",
            filetags=["test"],
            headings=[HeadingSpec(title="Notes", level=1)],
        )
        sexp = generate_roam_template_sexp(tpl)
        assert '"tn"' in sexp
        assert '"Test Note"' in sexp
        assert "file+head" in sexp
        assert ":test:" in sexp

    def test_generate_org_capture(self):
        tpl = CaptureTemplate(
            name="research",
            display_name="Research Agent",
            key="Ar",
            description="Research dispatch",
            template_type=TemplateType.ORG_CAPTURE,
            raw_body="- [ ] @research: %?",
        )
        sexp = generate_org_capture_sexp(tpl)
        assert "add-to-list" in sexp
        assert "org-capture-templates" in sexp
        assert '"Ar"' in sexp
        assert "eh/ensure-overnight-heading" in sexp

    def test_generate_full_elisp(self):
        templates = [
            CaptureTemplate(
                name="note",
                display_name="Note",
                key="n",
                description="Note",
                template_type=TemplateType.ROAM_CAPTURE,
                title_pattern="${title}",
            ),
            CaptureTemplate(
                name="agent",
                display_name="Agent Task",
                key="A",
                description="Agent dispatch",
                template_type=TemplateType.ORG_CAPTURE,
                raw_body="- [ ] @research: %?",
            ),
        ]
        elisp = generate_elisp(templates)
        assert "Generated by engineering-hub" in elisp
        assert "org-roam-capture-templates" in elisp
        assert "org-capture-templates" in elisp


# ---------------------------------------------------------------------------
# Applicator tests
# ---------------------------------------------------------------------------


class TestPlaceholderExpansion:
    def test_expand_simple(self):
        assert _expand_placeholders("Hello ${name}!", {"name": "World"}) == "Hello World!"

    def test_expand_missing_key_preserved(self):
        assert _expand_placeholders("${missing}", {}) == "${missing}"

    def test_expand_multiple(self):
        result = _expand_placeholders(
            "${client} - ${hours}h @ ${rate}/hr",
            {"client": "Acme", "hours": "3", "rate": "150"},
        )
        assert result == "Acme - 3h @ 150/hr"

    def test_expand_date_patterns(self):
        now = datetime(2026, 4, 10, 14, 30)
        result = _expand_date_patterns("%Y-%m-%d", now)
        assert result == "2026-04-10"

    def test_slugify(self):
        assert _slugify("Hello World!") == "hello-world"
        assert _slugify("Acme Corp - April 2026") == "acme-corp-april-2026"


class TestApplyTemplate:
    def test_apply_creates_file(self, tmp_path: Path):
        roam_dir = tmp_path / "org-roam"
        roam_dir.mkdir()

        tpl = CaptureTemplate(
            name="test",
            display_name="Test",
            key="t",
            description="Test template",
            target_dir="notes/",
            title_pattern="${title}",
            filetags=["test"],
            headings=[HeadingSpec(title="Notes", level=1)],
            fields=[FieldSpec(name="title", prompt="Title")],
        )

        now = datetime(2026, 4, 10, 14, 30, 0)
        values = {"title": "My Test Note"}

        ok, msg = apply_template(tpl, roam_dir, values, now=now)
        assert ok
        assert "Created:" in msg

        notes_dir = roam_dir / "notes"
        assert notes_dir.exists()
        files = list(notes_dir.glob("*.org"))
        assert len(files) == 1

        content = files[0].read_text()
        assert "#+title: My Test Note" in content
        assert "#+filetags: :test:" in content
        assert "* Notes" in content
        assert ":ID:" in content

    def test_apply_with_properties(self, tmp_path: Path):
        roam_dir = tmp_path / "org-roam"
        roam_dir.mkdir()

        tpl = CaptureTemplate(
            name="hours",
            display_name="Hours",
            key="h",
            description="Hour log",
            properties={"CLIENT": "${client}", "RATE": "${rate}"},
            title_pattern="${client} Hours",
            fields=[
                FieldSpec(name="client", prompt="Client"),
                FieldSpec(name="rate", prompt="Rate"),
            ],
        )

        values = {"client": "Acme", "rate": "150"}
        ok, msg = apply_template(tpl, roam_dir, values, now=datetime(2026, 4, 10))
        assert ok

        files = list(roam_dir.glob("*.org"))
        assert len(files) == 1
        content = files[0].read_text()
        assert ":CLIENT:    Acme" in content
        assert ":RATE:    150" in content
        assert "#+title: Acme Hours" in content

    def test_apply_with_agent_dispatch(self, tmp_path: Path):
        roam_dir = tmp_path / "org-roam"
        roam_dir.mkdir()
        journal_dir = tmp_path / "journal"
        journal_dir.mkdir()

        tpl = CaptureTemplate(
            name="dispatch",
            display_name="Dispatch",
            key="d",
            description="With dispatch",
            title_pattern="${title}",
            agent_dispatch=AgentDispatchSpec(
                agent_type="research",
                description_template="Look into ${title}",
                on=DispatchTrigger.ON_CAPTURE,
            ),
        )

        values = {"title": "Something"}
        ok, msg = apply_template(
            tpl, roam_dir, values, journal_dir=journal_dir,
            now=datetime(2026, 4, 10),
        )
        assert ok
        assert "Agent task queued" in msg

        # add_todo_to_journal uses datetime.now() internally for the filename,
        # so check today's actual date
        today_str = datetime.now().strftime("%Y-%m-%d")
        today_file = journal_dir / f"{today_str}.org"
        assert today_file.exists()
        content = today_file.read_text()
        assert "@research: Look into Something" in content
