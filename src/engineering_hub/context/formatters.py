"""Agent-specific context formatters."""

from engineering_hub.core.constants import AgentType
from engineering_hub.core.models import ProjectContext


class ContextFormatter:
    """Format project context for different agent types."""

    @classmethod
    def format(cls, context: ProjectContext, agent_type: AgentType) -> str:
        """Format context for the specified agent type.

        Args:
            context: Project context data
            agent_type: Type of agent to format for

        Returns:
            Formatted context string
        """
        formatter_map = {
            AgentType.RESEARCH: cls._format_for_research,
            AgentType.TECHNICAL_WRITER: cls._format_for_technical_writer,
            AgentType.STANDARDS_CHECKER: cls._format_for_standards_checker,
            AgentType.REF_ENGINEER: cls._format_for_ref_engineer,
            AgentType.EVALUATOR: cls._format_for_evaluator,
            AgentType.TECHNICAL_REVIEWER: cls._format_for_technical_reviewer,
        }

        formatter = formatter_map.get(agent_type, cls._format_default)
        base = formatter(context)

        memory_block = context.metadata.get("memory_context_block", "")
        if memory_block:
            base += "\n\n---\n\n" + memory_block

        corpus_block = context.metadata.get("corpus_context_block", "")
        if corpus_block:
            base += "\n\n---\n\n" + corpus_block

        return base

    @classmethod
    def _format_default(cls, context: ProjectContext) -> str:
        """Default context formatting."""
        return cls._format_for_research(context)

    @classmethod
    def _format_for_research(cls, context: ProjectContext) -> str:
        """Format context for research agent.

        Emphasizes scope, standards, and prior research.
        """
        lines = [
            f"## Project Context: {context.project.title}",
            "",
            "### Project Overview",
            f"- **Client**: {context.project.client_name}",
            f"- **Status**: {context.project.status}",
        ]

        if context.project.budget:
            lines.append(f"- **Budget**: ${context.project.budget}")

        if context.project.description:
            lines.extend(["", f"**Description**: {context.project.description}"])

        # Scope of work
        if context.scope:
            lines.extend(["", "### Scope of Work"])
            for item in context.scope:
                lines.append(f"- {item}")

        # Standards
        if context.standards:
            lines.extend(["", "### Standards & Requirements"])
            for std in context.standards:
                lines.append(f"- {std.id} ({std.type})")

        # Available files
        if context.recent_files:
            lines.extend(["", "### Available Project Files"])
            for f in context.recent_files:
                lines.append(f"- {f.title} ({f.file_type})")

        # Task-referenced documents
        lines.extend(cls._format_task_file_contents(context))

        # Research guidance
        lines.extend(
            [
                "",
                "### Research Focus",
                "Your research should focus on topics directly relevant to the scope items above.",
                "Ensure all findings align with the listed standards requirements.",
                "Flag any gaps or conflicts in requirements.",
            ]
        )

        return "\n".join(lines)

    @classmethod
    def _format_for_technical_writer(cls, context: ProjectContext) -> str:
        """Format context for technical writer agent.

        Emphasizes client background, document purpose, and style.
        """
        lines = [
            f"## Project Context: {context.project.title}",
            "",
            "### Client Information",
            f"- **Client**: {context.project.client_name}",
        ]

        # Add client technical level from metadata
        tech_level = context.metadata.get("client_technical_level", "moderate")
        lines.append(f"- **Technical Sophistication**: {tech_level}")

        if context.project.budget:
            lines.append(f"- **Budget**: ${context.project.budget}")

        # Document purpose from scope
        if context.scope:
            lines.extend(["", "### Document Purpose (from Scope)"])
            for item in context.scope:
                lines.append(f"- {item}")

        # Standards to reference
        if context.standards:
            lines.extend(["", "### Standards to Reference"])
            for std in context.standards:
                lines.append(f"- {std.id}")

        # Available research
        if context.recent_files:
            lines.extend(["", "### Available Research & Files"])
            for f in context.recent_files:
                lines.append(f"- [[{f.url or f.title}]] - {f.title}")

        # Staged source documents from ingest
        lines.extend(cls._format_staged_files_section(context))

        # Task-referenced documents
        lines.extend(cls._format_task_file_contents(context))

        # Writing guidance based on client level
        lines.extend(["", "### Writing Guidelines"])
        if tech_level == "low":
            lines.extend(
                [
                    "- Use plain language, avoid jargon",
                    "- Include visual aids and diagrams where possible",
                    "- Provide context for technical terms",
                    "- Focus on practical implications",
                ]
            )
        elif tech_level == "high":
            lines.extend(
                [
                    "- Use precise technical language",
                    "- Include detailed methodology",
                    "- Reference specific standard sections",
                    "- Provide comprehensive technical appendices",
                ]
            )
        else:  # moderate
            lines.extend(
                [
                    "- Balance technical accuracy with accessibility",
                    "- Define acronyms on first use",
                    "- Include executive summary for non-technical readers",
                    "- Provide technical details in appendices",
                ]
            )

        return "\n".join(lines)

    @classmethod
    def _format_staged_files_section(cls, context: ProjectContext) -> list[str]:
        """Format staged source files section for formatters."""
        staged = context.metadata.get("staged_source_files", [])
        if not staged:
            return []

        lines = ["", "### Staged Source Documents"]
        for f in staged:
            name = f.get("original_name", "unknown")
            path = f.get("staged_path", "")
            lines.append(f"- {name} → {path}")
        return lines

    @classmethod
    def _format_task_file_contents(
        cls,
        context: ProjectContext,
        max_chars_per_file: int = 40000,
        label_roles: bool = False,
    ) -> list[str]:
        """Format task-referenced file contents into a context section.

        Used by all agent formatters. When ``label_roles`` is True (used by the
        technical-reviewer formatter), .tex files are labeled as the Draft Document
        and .md/.pdf files are labeled as Review Comments, so the agent knows which
        role each file plays without having to infer it from the filename.
        """
        task_files = context.metadata.get("task_file_contents", [])
        if not task_files:
            return []

        lines = ["", "### Referenced Documents"]
        for tf in task_files:
            name = tf.get("name", "file")
            file_type = tf.get("file_type", "")

            if label_roles:
                if file_type == "tex":
                    role = "Draft Document"
                elif file_type in ("md", "pdf", "docx"):
                    role = "Review Comments"
                else:
                    role = file_type.upper() if file_type else "Document"
                label = f"{name} — {role}"
            else:
                label = f"{name} ({file_type})" if file_type else name

            lines.append(f"\n#### {label}")
            lines.append("```")
            content = tf.get("content", "")
            lines.append(
                content[:max_chars_per_file]
                + ("..." if len(content) > max_chars_per_file else "")
            )
            lines.append("```")
        return lines

    @classmethod
    def _format_for_standards_checker(cls, context: ProjectContext) -> str:
        """Format context for standards checker agent.

        Emphasizes standards requirements and compliance criteria.
        """
        lines = [
            f"## Project Context: {context.project.title}",
            "",
            "### Compliance Requirements",
        ]

        # Standards (primary focus)
        if context.standards:
            lines.append("")
            lines.append("**Required Standards:**")
            for std in context.standards:
                lines.append(f"- **{std.id}** ({std.type})")
        else:
            lines.append("- No specific standards listed (review scope for implicit requirements)")

        # Scope items to verify
        if context.scope:
            lines.extend(["", "### Scope Items to Verify"])
            for item in context.scope:
                lines.append(f"- {item}")

        # Available documents to check
        if context.recent_files:
            lines.extend(["", "### Documents Available for Review"])
            for f in context.recent_files:
                lines.append(f"- {f.title} ({f.file_type})")

        # Task-referenced documents
        lines.extend(cls._format_task_file_contents(context))

        # Compliance guidance
        lines.extend(
            [
                "",
                "### Compliance Check Instructions",
                "1. Verify all claims reference correct standard sections",
                "2. Check that standard versions match requirements (e.g., ASTM E336-17a not E336-14)",
                "3. Identify any missing required elements per standards",
                "4. Flag any conflicts between standards or scope items",
                "5. Note any gaps where standards requirements are not addressed",
            ]
        )

        return "\n".join(lines)

    @classmethod
    def _format_for_ref_engineer(cls, context: ProjectContext) -> str:
        """Format context for reference engineer (reviewer) agent.

        Emphasizes source verification and technical accuracy.
        """
        lines = [
            f"## Project Context: {context.project.title}",
            "",
            "### Review Context",
            f"- **Client**: {context.project.client_name}",
            f"- **Status**: {context.project.status}",
        ]

        # Standards for verification
        if context.standards:
            lines.extend(["", "### Standards for Verification"])
            for std in context.standards:
                lines.append(f"- {std.id}")

        # Scope for alignment check
        if context.scope:
            lines.extend(["", "### Scope (for alignment check)"])
            for item in context.scope:
                lines.append(f"- {item}")

        # Available source materials
        if context.recent_files:
            lines.extend(["", "### Available Source Materials"])
            for f in context.recent_files:
                lines.append(f"- {f.title}")

        # Review instructions
        lines.extend(
            [
                "",
                "### Review Instructions",
                "For each document reviewed, assess:",
                "1. **Technical Accuracy**: Are claims technically correct?",
                "2. **Source Verification**: Are all claims properly sourced?",
                "3. **Missing References**: What sources should be added?",
                "4. **Standards Alignment**: Does content align with required standards?",
                "5. **Revision Suggestions**: Specific improvements with references",
                "",
                "Provide an overall quality rating (1-10) for each document.",
            ]
        )

        return "\n".join(lines)

    @classmethod
    def _format_for_evaluator(cls, context: ProjectContext) -> str:
        """Format context for evaluator agent.

        Emphasizes selection criteria and comparison framework.
        """
        lines = [
            f"## Project Context: {context.project.title}",
            "",
            "### Evaluation Context",
            f"- **Client**: {context.project.client_name}",
        ]

        tech_level = context.metadata.get("client_technical_level", "moderate")
        lines.append(f"- **Target Audience Technical Level**: {tech_level}")

        # Scope as success criteria
        if context.scope:
            lines.extend(["", "### Success Criteria (from Scope)"])
            for item in context.scope:
                lines.append(f"- {item}")

        # Standards as quality bar
        if context.standards:
            lines.extend(["", "### Quality Standards"])
            for std in context.standards:
                lines.append(f"- {std.id}")

        # Evaluation framework
        lines.extend(
            [
                "",
                "### Evaluation Criteria",
                "Evaluate each version on:",
                "1. **Technical Accuracy** (30%): Correct information, proper methodology",
                "2. **Completeness** (25%): All scope items addressed",
                "3. **Clarity** (20%): Appropriate for target audience",
                "4. **Sourcing** (15%): Proper references and citations",
                "5. **Presentation** (10%): Professional formatting and structure",
                "",
                "Provide a structured comparison and clear selection rationale.",
            ]
        )

        return "\n".join(lines)

    @classmethod
    def _format_for_technical_reviewer(cls, context: ProjectContext) -> str:
        """Format context for technical reviewer agent."""
        lines = [
            f"## Project Context: {context.project.title}",
            "",
            "### Review Context",
            f"- **Client**: {context.project.client_name}",
            f"- **Status**: {context.project.status}",
        ]

        if context.standards:
            lines.extend(["", "### Standards for Verification"])
            for std in context.standards:
                lines.append(f"- {std.id}")

        if context.scope:
            lines.extend(["", "### Scope"])
            for item in context.scope:
                lines.append(f"- {item}")

        staged = context.metadata.get("staged_source_files", [])
        if staged:
            lines.extend(["", "### Staged Review Documents"])
            for f in staged:
                name = f.get("original_name", "unknown")
                path = f.get("staged_path", "")
                lines.append(f"- {name} -> {path}")
                for section in f.get("sections", []):
                    content = section.get("content", "")
                    if content:
                        lines.append("")
                        lines.append(content[:2000] + ("..." if len(content) > 2000 else ""))

        lines.extend(cls._format_task_file_contents(context, label_roles=True))

        lines.extend(
            [
                "",
                "### Review Instructions",
                "Follow the 5-phase workflow. For Phase 5 output, prepend a change log block and use % REVIEW_PENDING: for deferred items.",
            ]
        )

        return "\n".join(lines)
