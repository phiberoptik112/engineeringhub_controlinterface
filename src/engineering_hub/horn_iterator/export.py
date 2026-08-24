"""Result export and human-readable formatting for horn sweeps.

Writes CSV / org-table artifacts under an output directory and builds compact
markdown summaries for chat and CLI. Uses stdlib only so importing the tool
path stays lightweight.
"""

from __future__ import annotations

import csv
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Columns rendered in tables, in display order.
_COLUMNS: list[tuple[str, str]] = [
    ("l_exp_mm", "L_exp"),
    ("l_total_mm", "L_tot"),
    ("mouth_w_mm", "W"),
    ("mouth_h_mm", "H"),
    ("mouth_h_proj_mm", "H_proj"),
    ("mouth_area_mm2", "S_M"),
    ("fc_hz", "fc"),
    ("f_min_hz", "f_min"),
    ("fov_h_deg", "FOV_H"),
    ("fov_v_deg", "FOV_V"),
    ("spl_loss_min_freq_db", "Loss_LF"),
    ("status", "Status"),
]


def export_results(
    rows: list[dict[str, Any]],
    output_dir: Path | str,
    fmt: str = "csv",
    filename: str | None = None,
) -> dict[str, Any]:
    """Write sweep rows to ``output_dir`` as CSV or org-table text.

    Returns a result dict with ``success`` and the written ``path``.
    """
    fmt = fmt.lower().strip()
    if fmt not in ("csv", "org"):
        return {"success": False, "error": f"Unsupported format {fmt!r} (use csv|org)"}
    if not rows:
        return {"success": False, "error": "No rows to export"}

    out_dir = Path(output_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)

    if filename is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        suffix = "csv" if fmt == "csv" else "org"
        filename = f"horn_sweep_{stamp}.{suffix}"
    path = out_dir / filename

    try:
        if fmt == "csv":
            _write_csv(rows, path)
        else:
            path.write_text(_org_table(rows), encoding="utf-8")
    except OSError as exc:
        logger.warning("horn_iterator export failed: %s", exc)
        return {"success": False, "error": str(exc)}

    logger.info("horn_iterator: exported %d rows -> %s", len(rows), path)
    return {"success": True, "path": str(path), "format": fmt, "rows": len(rows)}


def _write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    fieldnames = list(rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _csv_cell(row.get(key)) for key in fieldnames})


def _csv_cell(value: Any) -> Any:
    if isinstance(value, list):
        return "; ".join(str(item) for item in value)
    return value


def _org_table(rows: list[dict[str, Any]]) -> str:
    headers = [label for _, label in _COLUMNS]
    lines = ["| " + " | ".join(headers) + " |"]
    lines.append("|" + "|".join(["---"] * len(headers)) + "|")
    for row in rows:
        cells = [str(row.get(key, "")) for key, _ in _COLUMNS]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def format_sweep_report(result: dict[str, Any], limit: int = 10) -> str:
    """Build a compact markdown summary of a sweep result dict.

    Accepts the dict returned by :func:`service.run_sweep` (with ``count``,
    ``valid_count``, ``rows``, ``valid``).
    """
    count = result.get("count", 0)
    valid_count = result.get("valid_count", 0)
    valid = result.get("valid", [])
    rows = result.get("rows", [])

    lines = [
        "## Horn Parametric Sweep",
        "",
        f"- Candidates evaluated: **{count}**",
        f"- Passing LVT constraints: **{valid_count}**",
    ]

    fc_hz = rows[0].get("fc_hz") if rows else None
    if fc_hz is not None:
        lines.append(f"- Cutoff frequency (fc): **{fc_hz} Hz**")
    lines.append("")

    top = valid[:limit] if valid else []
    if top:
        lines.append(f"### Top {len(top)} valid designs (best LF first)")
        lines.append("")
        lines.append(_markdown_table(top))
    else:
        lines.append("No designs passed all LVT constraints. "
                     "Inspect violations in the full results to relax bounds.")
        worst = rows[:limit]
        if worst:
            lines.append("")
            lines.append(f"### First {len(worst)} candidates (with violations)")
            lines.append("")
            lines.append(_markdown_table(worst, include_violations=True))

    return "\n".join(lines)


def _markdown_table(
    rows: list[dict[str, Any]],
    include_violations: bool = False,
) -> str:
    columns = list(_COLUMNS)
    if include_violations:
        columns = columns + [("violations", "Violations")]
    headers = [label for _, label in columns]
    out = ["| " + " | ".join(headers) + " |"]
    out.append("|" + "|".join(["---"] * len(headers)) + "|")
    for row in rows:
        cells = []
        for key, _ in columns:
            value = row.get(key, "")
            if isinstance(value, list):
                value = "; ".join(str(item) for item in value) or "-"
            cells.append(str(value))
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out)


def format_defaults_report(defaults: dict[str, Any]) -> str:
    """Render the configured constraints/bounds as readable markdown."""
    constraints = defaults.get("constraints", {})
    bounds = defaults.get("bounds", {})
    lines = ["## Horn Iterator Defaults", "", "### LVT Constraints"]
    for key, value in constraints.items():
        lines.append(f"- {key}: {value}")
    lines.append("")
    lines.append("### Sweep Bounds")
    for key, value in bounds.items():
        lines.append(f"- {key}: {value}")
    return "\n".join(lines)
