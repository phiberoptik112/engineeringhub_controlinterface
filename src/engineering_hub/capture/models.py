"""Data models for hub capture templates.

Unifies Emacs org-roam-capture-templates and org-capture-templates into a
single YAML-native representation that can be round-tripped to elisp.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class TemplateType(str, Enum):
    """Distinguishes org-roam-capture vs plain org-capture templates."""

    ROAM_CAPTURE = "roam-capture"
    ORG_CAPTURE = "org-capture"


class FieldType(str, Enum):
    """Supported field types for prompted values at capture time."""

    TEXT = "text"
    NUMBER = "number"
    DATE = "date"
    CHOICE = "choice"


class DispatchTrigger(str, Enum):
    """When to fire the agent dispatch associated with a capture template."""

    ON_CAPTURE = "on_capture"
    MANUAL = "manual"
    WEEKLY = "weekly"


@dataclass
class FieldSpec:
    """A prompted field filled at capture time."""

    name: str
    prompt: str
    type: FieldType = FieldType.TEXT
    default: str = ""
    choices: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d: dict = {"name": self.name, "prompt": self.prompt, "type": self.type.value}
        if self.default:
            d["default"] = self.default
        if self.choices:
            d["choices"] = self.choices
        return d

    @classmethod
    def from_dict(cls, data: dict) -> FieldSpec:
        return cls(
            name=data["name"],
            prompt=data.get("prompt", data["name"]),
            type=FieldType(data.get("type", "text")),
            default=data.get("default", ""),
            choices=data.get("choices", []),
        )


@dataclass
class HeadingSpec:
    """A heading in the generated org file structure."""

    title: str
    level: int = 1
    body: str = ""
    children: list[HeadingSpec] = field(default_factory=list)

    def to_dict(self) -> dict:
        d: dict = {"title": self.title}
        if self.level != 1:
            d["level"] = self.level
        if self.body:
            d["body"] = self.body
        if self.children:
            d["children"] = [c.to_dict() for c in self.children]
        return d

    @classmethod
    def from_dict(cls, data: dict, default_level: int = 1) -> HeadingSpec:
        children = [
            cls.from_dict(c, default_level=default_level + 1)
            for c in data.get("children", [])
        ]
        return cls(
            title=data["title"],
            level=data.get("level", default_level),
            body=data.get("body", ""),
            children=children,
        )


@dataclass
class AgentDispatchSpec:
    """Optional agent task dispatch triggered by a capture."""

    agent_type: str
    description_template: str
    on: DispatchTrigger = DispatchTrigger.MANUAL
    project_id: int | None = None

    def to_dict(self) -> dict:
        d: dict = {
            "agent_type": self.agent_type,
            "description_template": self.description_template,
            "on": self.on.value,
        }
        if self.project_id is not None:
            d["project_id"] = self.project_id
        return d

    @classmethod
    def from_dict(cls, data: dict) -> AgentDispatchSpec:
        return cls(
            agent_type=data["agent_type"],
            description_template=data.get("description_template", ""),
            on=DispatchTrigger(data.get("on", "manual")),
            project_id=data.get("project_id"),
        )


@dataclass
class CaptureTemplate:
    """A hub capture template that can generate org-roam nodes or journal tasks.

    Serves as the canonical in-memory representation for both YAML-defined
    hub templates and templates imported from Emacs config.el.
    """

    name: str
    display_name: str
    key: str
    description: str
    template_type: TemplateType = TemplateType.ROAM_CAPTURE

    # Target specification
    target_dir: str = ""
    filename_pattern: str = "%Y%m%d%H%M%S-${slug}.org"

    # Org file structure
    title_pattern: str = "${title}"
    filetags: list[str] = field(default_factory=list)
    properties: dict[str, str] = field(default_factory=dict)
    headings: list[HeadingSpec] = field(default_factory=list)

    # Prompted fields
    fields: list[FieldSpec] = field(default_factory=list)

    # Agent integration
    agent_dispatch: AgentDispatchSpec | None = None

    # Emacs extras preserved during import/export round-trip
    elisp_extras: dict = field(default_factory=dict)

    # Raw body text from Emacs (complex templates that don't decompose into headings)
    raw_body: str = ""

    # Source tracking
    source: str = "yaml"

    def to_dict(self) -> dict:
        """Serialize to a dict suitable for YAML output."""
        d: dict = {
            "name": self.name,
            "display_name": self.display_name,
            "key": self.key,
            "description": self.description,
            "template_type": self.template_type.value,
        }

        d["target"] = {
            "dir": self.target_dir,
            "filename": self.filename_pattern,
        }

        structure: dict = {"title": self.title_pattern}
        if self.filetags:
            structure["filetags"] = self.filetags
        if self.properties:
            structure["properties"] = dict(self.properties)
        if self.headings:
            structure["headings"] = [h.to_dict() for h in self.headings]
        d["structure"] = structure

        if self.fields:
            d["fields"] = [f.to_dict() for f in self.fields]

        if self.agent_dispatch:
            d["agent_dispatch"] = self.agent_dispatch.to_dict()

        if self.raw_body:
            d["raw_body"] = self.raw_body

        if self.elisp_extras:
            d["elisp_extras"] = dict(self.elisp_extras)

        return d

    @classmethod
    def from_dict(cls, data: dict) -> CaptureTemplate:
        """Deserialize from a YAML-loaded dict."""
        target = data.get("target", {})
        structure = data.get("structure", {})

        headings = [
            HeadingSpec.from_dict(h) for h in structure.get("headings", [])
        ]

        fields = [FieldSpec.from_dict(f) for f in data.get("fields", [])]

        agent_dispatch = None
        if "agent_dispatch" in data:
            agent_dispatch = AgentDispatchSpec.from_dict(data["agent_dispatch"])

        return cls(
            name=data.get("name", ""),
            display_name=data.get("display_name", data.get("name", "")),
            key=data.get("key", ""),
            description=data.get("description", ""),
            template_type=TemplateType(data.get("template_type", "roam-capture")),
            target_dir=target.get("dir", ""),
            filename_pattern=target.get("filename", "%Y%m%d%H%M%S-${slug}.org"),
            title_pattern=structure.get("title", "${title}"),
            filetags=structure.get("filetags", []),
            properties=structure.get("properties", {}),
            headings=headings,
            fields=fields,
            agent_dispatch=agent_dispatch,
            elisp_extras=data.get("elisp_extras", {}),
            raw_body=data.get("raw_body", ""),
            source=data.get("source", "yaml"),
        )
