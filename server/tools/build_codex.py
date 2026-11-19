"""Generate the read-only MinRTS codex from the planner's Markdown."""

from collections import Counter
from decimal import Decimal
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
DESIGN = ROOT / "Planner" / "MinRTS 장비·자원·능력치 설계서.md"
OUTPUT = ROOT / "server" / "game" / "codex.json"

if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))

from server.game.data import BATTALIONS

CATEGORIES = [
    {"id": "T", "name": "전차와 전차 기반 차량"},
    {"id": "A", "name": "장갑차"},
    {"id": "H", "name": "헬기"},
    {"id": "I", "name": "보병장비"},
    {"id": "E", "name": "전자전장비"},
    {"id": "S", "name": "공용 장비"},
]
TAGS = ["금속", "경량합금", "복합재", "기계부품", "전자부품", "광학부품"]
REFERENCE_FIELDS = {
    "공격력": "atk", "방어력": "dfn", "이동속도": "spd",
    "사거리": "rng", "체력": "hp", "생산비": "cost",
}

SECTION = re.compile(r"^## (\d+)\.[^\n]*\n", re.MULTILINE)
CARD = re.compile(r"^#### (EQ-([TAHIE])\d+) +(.+)$", re.MULTILINE)
BULLET = re.compile(r"^- \*\*(.+?)\*\* +(.+)$", re.MULTILINE)
BATTALION_ID = re.compile(r"`([^`]+)`")
PART_ID = re.compile(r"PART-([TAHIES])\d+")
VALUE = r"(\d+(?:\.\d+)?)"


def check_reference(card_id, battalion, stats, cost, warnings):
    baseline = BATTALIONS.get(battalion)
    if baseline is None:
        warnings.append(f"{card_id}: unknown battalion ID {battalion!r}")
        return

    for label, key in REFERENCE_FIELDS.items():
        if key == "cost":
            match = re.search(rf"자원\s+{VALUE}", cost)
        else:
            match = re.search(rf"(?:^|·)\s*{label}\s+{VALUE}", stats)
        if match is None:
            warnings.append(f"{card_id}: cannot read {label}")
        elif Decimal(match[1]) != Decimal(str(baseline[key])):
            warnings.append(
                f"{card_id} ({battalion}): {label} is {match[1]} in the design, "
                f"{baseline[key]} in data.py"
            )


def parse_equipment(section, warnings):
    cards = []
    expected = {"역할", "연결 대대", "주요 구성품", "대대 Lv.1 참조", "생산비", "비교 축", "적용 범위"}
    headers = list(CARD.finditer(section))
    for position, header in enumerate(headers):
        card_id, category, name = header.groups()
        end = headers[position + 1].start() if position + 1 < len(headers) else len(section)
        fields = {label: value.strip() for label, value in BULLET.findall(section, header.end(), end)}
        missing = expected.difference(fields)
        if missing:
            warnings.append(f"{card_id}: missing fields {', '.join(sorted(missing))}")
        match = BATTALION_ID.search(fields.get("연결 대대", ""))
        battalion = match[1] if match else ""
        check_reference(card_id, battalion, fields.get("대대 Lv.1 참조", ""),
                        fields.get("생산비", ""), warnings)
        parts = fields.get("주요 구성품", "").removesuffix(".").split("·")
        cards.append({
            "id": card_id, "category": category, "name": name.strip(),
            "role": fields.get("역할", ""), "battalion": battalion,
            "parts": [part.strip() for part in parts if part.strip()],
            "axis": fields.get("비교 축", "").removesuffix("."),
            "scope": fields.get("적용 범위", ""),
        })
    return cards


def parse_parts(section, warnings):
    parts = []
    for line in section.splitlines():
        line = line.strip()
        if not re.match(r"^\|\s*PART-", line):
            continue
        columns = [column.strip() for column in line.strip("|").split("|")]
        match = PART_ID.fullmatch(columns[0])
        if match is None or len(columns) != 5:
            warnings.append(f"Malformed part row: {line}")
            continue
        part_id, name, tag, compare_field, description = columns
        parts.append({"id": part_id, "category": match[1], "name": name,
                      "tag": tag, "field": compare_field, "desc": description})
    return parts


def build(markdown):
    sections = SECTION.split(markdown)
    sections = dict(zip(sections[1::2], sections[2::2], strict=True))
    warnings = []
    equipment = parse_equipment(sections.get("5", ""), warnings)
    parts = parse_parts(sections.get("6", ""), warnings)
    for records, expected, label in ((equipment, 70, "equipment"), (parts, 110, "parts")):
        if len(records) != expected:
            warnings.append(f"Expected {expected} {label}, found {len(records)}")
    for entry_id, count in Counter(entry["id"] for entry in equipment + parts).items():
        if count > 1:
            warnings.append(f"Duplicate ID {entry_id} ({count} occurrences)")
    return {"categories": CATEGORIES, "tags": TAGS, "equipment": equipment, "parts": parts}, warnings


def main():
    codex, warnings = build(DESIGN.read_text(encoding="utf-8-sig"))
    OUTPUT.write_text(json.dumps(codex, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8", newline="\n")
    for warning in warnings:
        print(f"WARNING: {warning}")
    print(f"Generated {OUTPUT.relative_to(ROOT).as_posix()} with "
          f"{len(codex['equipment'])} equipment cards, {len(codex['parts'])} parts, "
          f"and {len(warnings)} warnings.")


if __name__ == "__main__":
    main()
