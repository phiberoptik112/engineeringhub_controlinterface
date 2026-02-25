"""Agent tool schemas and handlers."""

import json
import logging
from pathlib import Path

from engineering_hub.actions.file_ingest import FileIngestAction

logger = logging.getLogger(__name__)

INGEST_FILES_TOOL = {
    "name": "ingest_files",
    "description": "Ingest files (PDF, DOCX) from a path into staging as markdown. Use when you need to read a file that hasn't been pre-staged.",
    "input_schema": {
        "type": "object",
        "properties": {
            "source_path": {
                "type": "string",
                "description": "Path to file or directory (e.g. ~/path/to/file.pdf)",
            },
            "project_id": {
                "type": "integer",
                "description": "Project ID for staging directory",
            },
        },
        "required": ["source_path", "project_id"],
    },
}


def handle_ingest_files(
    source_path: str,
    project_id: int,
    output_dir: Path,
    manifest_name: str = "manifest.json",
) -> str:
    """Execute ingest_files tool and return result as string."""
    action = FileIngestAction(output_dir=output_dir, manifest_name=manifest_name)
    result = action.execute(source_paths=[source_path], project_id=project_id)
    if result.success:
        return json.dumps({
            "success": True,
            "files_converted": result.files_converted,
            "manifest_path": result.manifest_path,
        })
    return json.dumps({
        "success": False,
        "error": result.error_message,
    })
