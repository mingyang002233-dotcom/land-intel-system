#!/usr/bin/env python3
"""
Land cadastral dry-run validator.

This script validates:
- county/city
- district
- section
- subsection
- parcel number

It performs no write operations to Excel, SQLite, or Google Drive.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REF_PATH = PROJECT_ROOT / "data" / "reference" / "land_section_codes.csv"

FIELD_ALIASES = {
    "county_city": ("county_city", "縣市", "縣市名稱"),
    "district": ("district", "地區", "鄉鎮市區", "鄉鎮市區名稱", "行政區"),
    "section": ("section", "地段", "段"),
    "subsection": ("subsection", "小段"),
    "parcel_no": ("parcel_no", "地號", "land_no", "land_no_raw"),
}

PARCEL_RE = re.compile(r"^(\d{1,4})(?:-(\d{1,4}))?$")


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def normalize_city(value: Any) -> str:
    return normalize_text(value).replace("台", "臺")


def canonical_section_name(value: Any) -> str:
    text = normalize_text(value)
    if not text:
        return ""
    return text[:-1] if text.endswith("段") else text


def canonical_subsection_name(value: Any) -> str:
    text = normalize_text(value)
    if not text:
        return ""
    return text[:-2] if text.endswith("小段") else text


def display_section_name(base_name: str) -> str:
    return f"{base_name}段" if base_name else ""


def display_subsection_name(base_name: str) -> str:
    return f"{base_name}小段" if base_name else ""


def build_lookup_name(section_base: str, subsection_base: str) -> str:
    if not section_base:
        return ""
    if subsection_base:
        return f"{display_section_name(section_base)}{display_subsection_name(subsection_base)}"
    return display_section_name(section_base)


def normalize_parcel_no(value: Any) -> str | None:
    text = normalize_text(value)
    if not text:
        return None

    normalized = (
        text.replace("－", "-")
        .replace("—", "-")
        .replace("–", "-")
        .replace("─", "-")
        .replace("﹣", "-")
    )
    normalized = re.sub(r"\s*-\s*", "-", normalized)

    match = PARCEL_RE.fullmatch(normalized)
    if not match:
        return None

    main_no = match.group(1).zfill(4)
    sub_no = match.group(2)
    if sub_no is None:
        return main_no
    return f"{main_no}-{sub_no.zfill(4)}"


@dataclass(frozen=True)
class SectionRef:
    county_city: str
    district: str
    section_base: str
    subsection_base: str
    section_code: str
    office: str

    @property
    def lookup_name(self) -> str:
        return build_lookup_name(self.section_base, self.subsection_base)


class ReferenceIndex:
    def __init__(self, ref_path: Path) -> None:
        self.ref_path = ref_path
        self._index: dict[tuple[str, str, str, str], SectionRef] = {}
        self._load()

    def _load(self) -> None:
        with self.ref_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                county_city = normalize_city(row.get("縣市名稱"))
                district = normalize_text(row.get("鄉鎮市區名稱"))
                section_base = canonical_section_name(row.get("段名"))
                subsection_base = canonical_subsection_name(row.get("小段名"))
                key = (county_city, district, section_base, subsection_base)
                self._index[key] = SectionRef(
                    county_city=county_city,
                    district=district,
                    section_base=section_base,
                    subsection_base=subsection_base,
                    section_code=normalize_text(row.get("段代碼")),
                    office=normalize_text(row.get("地政事務所")),
                )

    def lookup(
        self,
        county_city: str,
        district: str,
        section_base: str,
        subsection_base: str,
    ) -> SectionRef | None:
        return self._index.get((county_city, district, section_base, subsection_base))


class LandCadastralValidator:
    def __init__(self, reference_index: ReferenceIndex) -> None:
        self.reference_index = reference_index

    def validate_record(self, record: dict[str, Any]) -> dict[str, Any]:
        errors: list[str] = []
        suggested_fix: list[str] = []

        original, field_presence = self._extract_original(record)

        county_city = normalize_city(original["county_city"])
        district = normalize_text(original["district"])
        section_base = canonical_section_name(original["section"])
        subsection_base = canonical_subsection_name(original["subsection"])
        parcel_normalized = normalize_parcel_no(original["parcel_no"])

        if not field_presence["county_city"] or not county_city:
            errors.append("縣市空白")
            suggested_fix.append("補上縣市欄位")
        if not field_presence["district"] or not district:
            errors.append("地區空白")
            suggested_fix.append("補上地區欄位")
        if not field_presence["section"] or not section_base:
            errors.append("地段空白")
            suggested_fix.append("補上地段欄位")
        if not field_presence["subsection"]:
            errors.append("小段欄位不存在")
            suggested_fix.append("建立小段欄位，即使內容可為空白")
        if not field_presence["parcel_no"] or not normalize_text(original["parcel_no"]):
            errors.append("地號空白")
            suggested_fix.append("補上地號欄位")
        elif parcel_normalized is None:
            errors.append("地號格式錯誤")
            suggested_fix.append("將地號改為 #### 或 ####-#### 格式")

        matched_ref: SectionRef | None = None
        can_lookup_section = (
            field_presence["county_city"]
            and field_presence["district"]
            and field_presence["section"]
            and field_presence["subsection"]
            and bool(county_city)
            and bool(district)
            and bool(section_base)
        )
        if can_lookup_section:
            matched_ref = self.reference_index.lookup(
                county_city=county_city,
                district=district,
                section_base=section_base,
                subsection_base=subsection_base,
            )
            if matched_ref is None:
                errors.append("段代碼未命中")
                suggested_fix.append("確認地段與小段名稱是否與官方資料一致")

        normalized = {
            "county_city": county_city,
            "district": district,
            "section": display_section_name(section_base),
            "subsection": display_subsection_name(subsection_base),
            "section_lookup_name": build_lookup_name(section_base, subsection_base),
            "parcel_no": parcel_normalized or "",
            "section_code": matched_ref.section_code if matched_ref else "",
        }

        return {
            "original": original,
            "normalized": normalized,
            "status": "failed" if errors else "passed",
            "errors": dedupe(errors),
            "suggested_fix": dedupe(suggested_fix),
            "writable": not errors,
        }

    def _extract_original(self, record: dict[str, Any]) -> tuple[dict[str, str], dict[str, bool]]:
        extracted: dict[str, str] = {}
        field_presence: dict[str, bool] = {}

        for canonical_name, aliases in FIELD_ALIASES.items():
            found = False
            value = ""
            for alias in aliases:
                if alias in record:
                    found = True
                    value = normalize_text(record.get(alias))
                    break
            extracted[canonical_name] = value
            field_presence[canonical_name] = found

        return extracted, field_presence


def dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            ordered.append(value)
    return ordered


def load_records_from_json(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        return [payload]
    if isinstance(payload, list):
        if not all(isinstance(item, dict) for item in payload):
            raise ValueError("JSON array must contain objects only.")
        return payload
    raise ValueError("JSON input must be an object or an array of objects.")


def load_records_from_csv(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader)


def parse_record_arg(raw: str) -> dict[str, Any]:
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("--record must be a JSON object.")
    return payload


def load_records(args: argparse.Namespace) -> list[dict[str, Any]]:
    if args.record:
        return [parse_record_arg(args.record)]
    if not args.input:
        raise ValueError("Provide either --record or --input.")

    input_path = Path(args.input)
    suffix = input_path.suffix.lower()
    if suffix == ".json":
        return load_records_from_json(input_path)
    if suffix == ".csv":
        return load_records_from_csv(input_path)
    raise ValueError("Unsupported input format. Use .json or .csv.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Dry-run validator for land cadastral fields.")
    parser.add_argument("--input", help="Path to a JSON or CSV file.")
    parser.add_argument("--record", help="Single JSON object for direct validation.")
    parser.add_argument(
        "--ref",
        default=str(DEFAULT_REF_PATH),
        help=f"Reference CSV path. Default: {DEFAULT_REF_PATH}",
    )
    parser.add_argument("--output", help="Optional path to write JSON results.")
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON output.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        ref_path = Path(args.ref)
        if not ref_path.exists():
            raise FileNotFoundError(f"Reference CSV not found: {ref_path}")

        records = load_records(args)
        validator = LandCadastralValidator(ReferenceIndex(ref_path))
        results = [validator.validate_record(record) for record in records]
        payload: dict[str, Any] | list[dict[str, Any]]
        payload = results[0] if args.record and len(results) == 1 else results

        dump_kwargs = {"ensure_ascii": False}
        if args.pretty:
            dump_kwargs["indent"] = 2

        output_text = json.dumps(payload, **dump_kwargs)
        if args.output:
            Path(args.output).write_text(output_text + "\n", encoding="utf-8")
        else:
            print(output_text)
        return 0
    except Exception as exc:  # pragma: no cover
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
