"""Read declared PDF drawing columns; never split an ambiguous flattened name.

Used only for audited source layouts during offline intake. Coordinates and
headings must be verified against that exact manufacturer's captured document.
"""

from bisect import bisect_right
from collections import defaultdict
import re
import unicodedata


def text_fingerprint(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFC", value)).casefold()


def drawing_pages(path):
    from pypdf import PdfReader

    pages = []
    for page in PdfReader(path).pages:
        fields = []

        def visitor(text, cm, tm, font, size):
            if text.strip():
                if tuple(cm[:4]) != (1, 0, 0, 1):
                    raise ValueError("Undeclared PDF drawing transformation")
                fields.append((" ".join(text.split()), float(tm[4] + cm[4]), float(tm[5] + cm[5])))

        page.extract_text(visitor_text=visitor)
        pages.append(fields)
    return pages


def parse_drawn_table(
    pages,
    *,
    headings,
    column_edges,
    name_columns,
    team_column,
    allow_empty_team=False,
    allow_wrapped_cells=False,
    wrapped_cell_labels=(),
):
    heading_keys = {text_fingerprint(value): value.upper() for value in headings}
    wrapped_keys = {text_fingerprint(value) for value in wrapped_cell_labels}
    rows, section = [], None
    for page_number, fields in enumerate(pages, 1):
        groups = []
        for text, x, y in sorted(fields, key=lambda f: (-f[2], f[1])):
            group = next((g for g in groups if abs(g[0] - y) <= 1.6), None)
            if group is None:
                group = [y, []]
                groups.append(group)
            group[1].append((text, x, y))
        edges = column_edges(page_number)
        pending_fields = []
        for line, (y, values) in enumerate(groups, 1):
            values.sort(key=lambda f: f[1])
            raw = " ".join(f[0] for f in values)
            if text_fingerprint(raw) in heading_keys:
                pending_fields = []
                section = heading_keys[text_fingerprint(raw)]
                continue
            cells = defaultdict(list)
            for text, x, _ in values:
                cells[bisect_right(edges, x)].append(text)
            number = "".join(cells[0]).strip()
            if not re.fullmatch(r"(?:\d+|[A-Za-z0-9]+(?:[-/][A-Za-z0-9]+)+)", number):
                if (
                    allow_wrapped_cells
                    and text_fingerprint(raw) in wrapped_keys
                    and all(x >= edges[0] for _, x, _ in values)
                ):
                    pending_fields.extend(values)
                    continue
                pending_fields = []
                section = None
                continue
            if pending_fields:
                values = pending_fields + values
                pending_fields = []
                cells = defaultdict(list)
                for text, x, _ in values:
                    cells[bisect_right(edges, x)].append(text)
            player = " ".join(" ".join(cells[n]) for n in name_columns).strip()
            team = " ".join(cells[team_column]).strip()
            if not section or not player or (not team and not allow_empty_team):
                continue
            player = re.sub(r"\s*-\s*", "-", player)
            rows.append(
                dict(
                    card_number=number,
                    player=player,
                    team=team or None,
                    section=section,
                    insert=None if section.startswith("BASE") else section.title(),
                    source_page=page_number,
                    source_line=line,
                    source_y=y,
                    raw_text=raw,
                    raw_pdf_drawing_fields=[dict(text=t, x=x, y=fy) for t, x, fy in values],
                    source_locator_basis="pypdf drawing fields grouped by baseline; declared columns",
                )
            )
    return rows
