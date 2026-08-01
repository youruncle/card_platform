import asyncio
import json
import platform
import re
import shutil
import tempfile
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[4] / "scripts" / "ocr_card_images.swift"


def available() -> bool:
    return (
        platform.system() == "Darwin"
        and SCRIPT_PATH.is_file()
        and shutil.which("swift") is not None
    )


def parse_ocr_output(payload: str) -> list[str]:
    rows = json.loads(payload)
    lines: list[str] = []
    for row in rows:
        if row.get("error"):
            continue
        lines.extend(
            text.strip()
            for text in row.get("lines") or []
            if isinstance(text, str) and text.strip()
        )
    return lines


def extract_anchors(lines: list[str]) -> dict:
    """Extract only high-precision identity anchors from on-device OCR text."""
    anchors: dict = {}
    season_candidates = []
    for line in lines:
        normalized = line.casefold()
        if any(
            marker in normalized
            for marker in ("drafted", "born:", "nba record", "career", "year team")
        ):
            continue
        for match in re.finditer(r"\b((?:19|20)\d{2})\s*[-/]\s*(\d{2,4})\b", line):
            start = int(match.group(1))
            raw_end = match.group(2)
            end = int(raw_end) if len(raw_end) == 4 else (start // 100) * 100 + int(raw_end)
            if end < start:
                end += 100
            score = 1
            if any(
                marker in normalized
                for marker in (
                    "panini",
                    "topps",
                    "upper deck",
                    "basketball",
                    "hoops",
                    "prizm",
                    "chrome",
                    "treasures",
                    "inception",
                )
            ):
                score += 4
            season_candidates.append((score, start, end, line))
    if season_candidates:
        _, start, end, evidence = max(season_candidates)
        anchors.update(
            season_start=start,
            season_end=end,
            season_evidence=evidence,
        )

    for line in lines:
        match = re.search(
            r"(?:\bNO\.?|N[º°]|#)\s*([A-Z0-9][A-Z0-9-]{0,24})\b",
            line,
            re.I,
        )
        if match:
            anchors["card_number"] = match.group(1).upper()
            anchors["card_number_evidence"] = line
            break

    serial_candidates = []
    for line in lines:
        for match in re.finditer(r"\b0*(\d{1,4})\s*/\s*0*(\d{1,4})\b", line):
            number, total = int(match.group(1)), int(match.group(2))
            if 0 < number <= total <= 1000:
                serial_candidates.append((total, number, line))
    if serial_candidates:
        total, number, evidence = min(serial_candidates)
        anchors.update(
            is_numbered=True,
            serial_number=number,
            serial_total=total,
            serial_evidence=evidence,
        )

    text = "\n".join(lines)
    if re.search(r"(?:^|\W)(?:RC|ROOKIE)(?:\W|$)", text, re.I):
        anchors["is_rookie"] = True
        anchors["rookie_evidence"] = next(
            line for line in lines if re.search(r"\b(?:RC|ROOKIE)\b", line, re.I)
        )
    autograph_line = next(
        (
            line
            for line in lines
            if re.search(
                r"(?:CERTIFIED AUTOGRAPH|AUTOGRAPH (?:IS )?GUARANTEED|AUTOGRAPH ISSUE)",
                line,
                re.I,
            )
        ),
        None,
    )
    if autograph_line:
        anchors["is_autograph"] = True
        anchors["autograph_evidence"] = autograph_line
    material_line = next(
        (
            line
            for line in lines
            if re.search(
                r"(?:ENCLOSED|GAME[- ]WORN|PLAYER[- ]WORN).{0,40}(?:MATERIAL|MEMORABILIA)",
                line,
                re.I,
            )
        ),
        None,
    )
    if material_line:
        anchors["is_memorabilia"] = True
        anchors["material_evidence"] = material_line
        if re.search(r"game[- ]worn", material_line, re.I):
            anchors["memorabilia_usage"] = "game_used"
        elif re.search(r"player[- ]worn", material_line, re.I):
            anchors["memorabilia_usage"] = "player_worn"
    return anchors


async def recognize_images(
    images: list[tuple[bytes, str]],
    *,
    timeout_seconds: float = 30,
) -> list[str]:
    """Read deterministic on-device OCR anchors without sending images elsewhere."""
    if not images or not available():
        return []
    with tempfile.TemporaryDirectory(prefix="card-platform-ocr-") as directory:
        paths = []
        for index, (content, content_type) in enumerate(images):
            suffix = ".png" if "png" in content_type.casefold() else ".jpg"
            path = Path(directory) / f"card-{index}{suffix}"
            path.write_bytes(content)
            paths.append(str(path))
        process = await asyncio.create_subprocess_exec(
            "swift",
            str(SCRIPT_PATH),
            *paths,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, _ = await asyncio.wait_for(
                process.communicate(), timeout=timeout_seconds
            )
        except TimeoutError:
            process.kill()
            await process.communicate()
            return []
        if process.returncode != 0:
            return []
        try:
            return parse_ocr_output(stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
            return []
