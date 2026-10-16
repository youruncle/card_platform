#!/usr/bin/env python3
"""Download public Panini basketball checklists using the official site's API."""
import argparse
import csv
from datetime import datetime, timezone
from hashlib import sha256
import io
import json
from pathlib import Path
import time

import httpx

ROOT = Path(__file__).resolve().parents[1] / "data/official-checklists/panini"
API = "https://support.paniniamerica.net"


def save(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    temporary.replace(path)


def select_window(products, start, end):
    selected = []
    for item in products:
        release = item.get("release_date")
        if release and not start <= release <= end:
            item["status"] = "excluded"
        else:
            if item.get("status") != "downloaded":
                item["status"] = "pending"
            selected.append(item)
    return selected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="1996-10-04")
    parser.add_argument("--end", default="2026-10-04")
    parser.add_argument("--refresh-directory", action="store_true")
    args = parser.parse_args()
    ROOT.mkdir(parents=True, exist_ok=True)
    report = {"started_at": datetime.now(timezone.utc).isoformat(),
              "window": {"start": args.start, "end": args.end},
              "directory_complete": False, "products": [], "errors": []}
    cached = ROOT / "progress.json"
    if cached.exists() and not args.refresh_directory:
        report = json.loads(cached.read_text())
        report["window"] = {"start": args.start, "end": args.end}
    payload = dict(activity="", year="", brand="", program="", card_set="",
                   card="", replace_wo_inventory="1", from_frontend="0")
    with httpx.Client(timeout=60) as client:
        def request(endpoint, **values):
            for attempt in range(3):
                try:
                    response = client.post(API + endpoint, json=payload | values)
                    response.raise_for_status()
                    return response
                except httpx.HTTPError:
                    if attempt == 2:
                        raise
                    time.sleep(attempt + 1)

        def options(**values):
            response = request("/replacement-card-selection", **values).json()
            if response.get("status") != 200:
                raise ValueError(str(response))
            return response["data"]

        activity = "9"
        if not report.get("directory_complete") or args.refresh_directory:
            sports = options()
            activity = str(next(x["id"] for x in sports if x["name"] == "Basketball"))
            years = options(activity=activity)
            report["archive_years"] = years
            for index, year_item in enumerate(years):
                year = str(year_item["name"])
                if year in report.get("completed_years", []):
                    continue
                report["errors"] = [e for e in report["errors"] if e.get("year") != year]
                year_products = []
                try:
                    for brand_item in options(activity=activity, year=year):
                        brand = brand_item["name"]
                        programs = options(activity=activity, year=year, brand=brand, from_frontend="2")
                        for program in programs:
                            release = (program.get("release_date") or "")[:10]
                            item = dict(program, archive_year=year, brand=brand,
                                        release_date=release or None,
                                        status="pending" if not release or args.start <= release <= args.end else "excluded")
                            year_products.append(item)
                    previous = {(p["archive_year"],p["id"]):p for p in report["products"]}
                    for item in year_products:
                        old=previous.get((year,item["id"]),{})
                        for field in ("checksum","file","rows","status"):
                            if field in old: item[field]=old[field]
                    report["products"] = [p for p in report["products"] if p["archive_year"] != year] + year_products
                    report.setdefault("completed_years", []).append(year)
                except Exception as exc:
                    report["errors"].append({"year": year, "error": str(exc)})
                report["directory_percent"] = round(100 * len(report.get("completed_years", [])) / len(years), 1)
                save(ROOT / "progress.json", report)
                print(f"Directory {index + 1}/{len(years)} years", flush=True)
            report["directory_complete"] = not report["errors"]
        selected = select_window(report["products"], args.start, args.end)
        for index, item in enumerate(selected):
            path = ROOT / f"{item['archive_year']}-{item['id']}.csv"
            try:
                if path.exists() and item.get("checksum") and sha256(path.read_bytes()).hexdigest() != item["checksum"]:
                    path.rename(path.with_suffix(f".corrupt-{time.time_ns()}"))
                if not path.exists():
                    response = request("/get-physical-checklist", activity=activity,
                                       year=item["archive_year"], brand=item["brand"], program=str(item["id"]))
                    if "text/csv" not in response.headers.get("content-type", ""):
                        raise ValueError("Expected CSV response")
                    temporary = path.with_suffix(".download")
                    temporary.write_bytes(response.content)
                    check_rows = list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))
                    if not check_rows or not {"CARD SET", "ATHLETE", "CARD NUMBER"} <= check_rows[0].keys():
                        raise ValueError("Missing checklist rows or columns")
                    temporary.replace(path)
                rows = list(csv.DictReader(io.StringIO(path.read_text(encoding="utf-8-sig"))))
                if not rows or not {"CARD SET", "ATHLETE", "CARD NUMBER"} <= rows[0].keys():
                    raise ValueError("Missing checklist rows or columns")
                item.update(status="downloaded", rows=len(rows), file=str(path.relative_to(ROOT)),
                            checksum=sha256(path.read_bytes()).hexdigest())
            except Exception as exc:
                if isinstance(exc, ValueError) and path.exists():
                    path.rename(path.with_suffix(f".invalid-{time.time_ns()}"))
                item.update(status="failed", error=str(exc))
            report["download_success_percent"] = round(100 * sum(x["status"] == "downloaded" for x in selected) / len(selected), 1)
            report["download_percent"] = round(100 * (index + 1) / len(selected), 1)
            save(ROOT / "progress.json", report)
            time.sleep(0.2)
            print(f"Download {index + 1}/{len(selected)} {item['name']}: {item['status']}", flush=True)
        save(ROOT / "progress.json", report)


if __name__ == "__main__":
    main()
