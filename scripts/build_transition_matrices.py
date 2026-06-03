"""將 YouBike OD GeoJSON 轉成 Dummy Node 架構使用的 transition matrix。

本腳本刻意不建立完整 station-to-station 巨大矩陣，而是拆成：
1. 站點出發選擇：同區站點 or 區外 Dummy Node。
2. 區外 Dummy Node 選擇目的行政區。
3. 進入目的行政區後選擇目的站點。

起點站 = 終點站的資料會被獨立抽出，不放進一般移動矩陣。
原因是它可能代表壞車立即歸還、臨時取消租借、或真實來回旅次；
直接刪除會讓需求量失真，直接混入 OD 又會讓一般移動偏向自我循環。
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any


OUT_OF_DISTRICT = "__OUT_OF_DISTRICT__"


@dataclass(frozen=True)
class OdRecord:
    """一筆已清理的 OD 聚合資料。"""

    origin_station_id: str
    destination_station_id: str
    origin_station_name: str
    destination_station_name: str
    origin_district: str
    destination_district: str
    count: int

    @property
    def is_self_station(self) -> bool:
        return self.origin_station_id == self.destination_station_id

    @property
    def is_cross_district(self) -> bool:
        return self.origin_district != self.destination_district


def infer_profile(path: Path) -> str:
    """從檔名判斷週間或週末資料。"""

    name = path.name
    if "週間" in name or "weekday" in name.lower():
        return "weekday"
    if "週末" in name or "weekend" in name.lower():
        return "weekend"
    raise ValueError(f"無法從檔名判斷資料類型：{path.name}")


def load_static_station_info(path: Path) -> dict[str, dict[str, str]]:
    """讀取站點靜態資料，用來檢查 OD 中的 station id 是否能對上。"""

    stations: dict[str, dict[str, str]] = {}
    if not path.exists():
        return stations

    with path.open("r", encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            station_id = str(row["sno"]).strip()
            stations[station_id] = {
                "name": row.get("sna", "").strip(),
                "district": row.get("sarea", "").strip(),
                "latitude": row.get("latitude", "").strip(),
                "longitude": row.get("longitude", "").strip(),
            }
    return stations


def load_cleaning_rules(path: Path | None) -> dict[str, Any]:
    """讀取行政區別名與排除站點規則。"""

    if path is None or not path.exists():
        return {"district_aliases": {}, "excluded_station_ids": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        "district_aliases": {
            str(source): str(target)
            for source, target in data.get("district_aliases", {}).items()
        },
        "excluded_station_ids": {
            str(station_id): str(reason)
            for station_id, reason in data.get("excluded_station_ids", {}).items()
        },
    }


def load_valid_station_ids(path: Path | None) -> set[str] | None:
    """讀取允許納入模擬的 station id；通常來自 static station 檔。"""

    if path is None or not path.exists():
        return None
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return {str(row["sno"]).strip() for row in csv.DictReader(file)}


def load_od_records(
    path: Path,
    district_aliases: dict[str, str] | None = None,
    excluded_station_ids: set[str] | None = None,
    valid_station_ids: set[str] | None = None,
) -> tuple[list[OdRecord], dict[str, Any]]:
    """從 GeoJSON 讀出 OD records。

    GeoJSON 幾何線段目前只用於原始資料視覺化；建立 transition matrix 時只需要 properties。
    """

    data = json.loads(path.read_text(encoding="utf-8"))
    records: list[OdRecord] = []
    district_aliases = district_aliases or {}
    excluded_station_ids = excluded_station_ids or set()
    skipped_by_excluded_station = 0
    skipped_by_invalid_station = 0

    for feature in data.get("features", []):
        props = feature.get("properties", {})
        origin_station_id = str(props["on_stop_id"]).strip()
        destination_station_id = str(props["off_stop_id"]).strip()
        if (
            origin_station_id in excluded_station_ids
            or destination_station_id in excluded_station_ids
        ):
            skipped_by_excluded_station += int(props["sum_of_txn_times"])
            continue
        if valid_station_ids is not None and (
            origin_station_id not in valid_station_ids
            or destination_station_id not in valid_station_ids
        ):
            skipped_by_invalid_station += int(props["sum_of_txn_times"])
            continue

        origin_district = str(props["district_origin"]).strip()
        destination_district = str(props["district_destination"]).strip()
        records.append(
            OdRecord(
                origin_station_id=origin_station_id,
                destination_station_id=destination_station_id,
                origin_station_name=str(props.get("on_stop", "")).strip(),
                destination_station_name=str(props.get("off_stop", "")).strip(),
                origin_district=district_aliases.get(origin_district, origin_district),
                destination_district=district_aliases.get(
                    destination_district,
                    destination_district,
                ),
                count=int(props["sum_of_txn_times"]),
            )
        )
    return records, {
        "skipped_transactions_by_excluded_station": skipped_by_excluded_station,
        "skipped_transactions_by_invalid_station": skipped_by_invalid_station,
        "district_aliases": district_aliases,
        "excluded_station_ids": sorted(excluded_station_ids),
    }


def add_count(nested: dict[str, Any], keys: tuple[str, ...], count: int) -> None:
    """在任意層級的 nested dict 中累加 count。"""

    current: dict[str, Any] = nested
    for key in keys[:-1]:
        current = current.setdefault(key, {})
    current[keys[-1]] = current.get(keys[-1], 0) + count


def normalize_counts(counts: dict[str, int]) -> dict[str, float]:
    """將 count dict 正規化成機率 dict。"""

    total = sum(counts.values())
    if total <= 0:
        return {}
    return {key: value / total for key, value in sorted(counts.items()) if value > 0}


def normalize_two_level(counts: dict[str, dict[str, int]]) -> dict[str, dict[str, float]]:
    return {row: normalize_counts(columns) for row, columns in sorted(counts.items())}


def normalize_three_level(
    counts: dict[str, dict[str, dict[str, int]]],
) -> dict[str, dict[str, dict[str, float]]]:
    return {
        outer_key: normalize_two_level(inner)
        for outer_key, inner in sorted(counts.items())
    }


def build_profile_matrices(
    profile: str,
    records: list[OdRecord],
    static_stations: dict[str, dict[str, str]],
) -> dict[str, Any]:
    """建立單一 profile（weekday 或 weekend）的所有 transition matrix。"""

    station_exit_counts: dict[str, dict[str, dict[str, int]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    inter_district_counts: dict[str, dict[str, int]] = defaultdict(dict)
    inbound_station_counts: dict[str, dict[str, int]] = defaultdict(dict)
    inbound_station_by_od_counts: dict[str, dict[str, dict[str, int]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    self_station_records: list[OdRecord] = []
    station_to_district: dict[str, str] = {}
    missing_static_station_ids: set[str] = set()

    total_count = 0
    self_count = 0
    cross_count = 0
    same_district_moving_count = 0

    for record in records:
        total_count += record.count
        station_to_district[record.origin_station_id] = record.origin_district
        station_to_district[record.destination_station_id] = record.destination_district

        if record.origin_station_id not in static_stations:
            missing_static_station_ids.add(record.origin_station_id)
        if record.destination_station_id not in static_stations:
            missing_static_station_ids.add(record.destination_station_id)

        if record.is_self_station:
            self_count += record.count
            self_station_records.append(record)
            continue

        if record.is_cross_district:
            cross_count += record.count
            add_count(
                station_exit_counts,
                (record.origin_district, record.origin_station_id, OUT_OF_DISTRICT),
                record.count,
            )
            add_count(
                inter_district_counts,
                (record.origin_district, record.destination_district),
                record.count,
            )
            add_count(
                inbound_station_counts,
                (record.destination_district, record.destination_station_id),
                record.count,
            )
            add_count(
                inbound_station_by_od_counts,
                (
                    record.origin_district,
                    record.destination_district,
                    record.destination_station_id,
                ),
                record.count,
            )
            continue

        same_district_moving_count += record.count
        add_count(
            station_exit_counts,
            (
                record.origin_district,
                record.origin_station_id,
                record.destination_station_id,
            ),
            record.count,
        )

    return {
        "profile": profile,
        "metadata": {
            "total_transactions": total_count,
            "self_station_transactions": self_count,
            "self_station_ratio": self_count / total_count if total_count else 0,
            "cross_district_transactions": cross_count,
            "cross_district_ratio_excluding_self": (
                cross_count / (total_count - self_count)
                if total_count > self_count
                else 0
            ),
            "same_district_moving_transactions": same_district_moving_count,
            "station_count_in_od": len(station_to_district),
            "district_count_in_od": len(set(station_to_district.values())),
            "missing_static_station_id_count": len(missing_static_station_ids),
            "out_of_district_key": OUT_OF_DISTRICT,
        },
        "station_to_district": dict(sorted(station_to_district.items())),
        "station_exit_transition_by_district": normalize_three_level(station_exit_counts),
        "inter_district_transition": normalize_two_level(inter_district_counts),
        "inbound_station_transition_by_district": normalize_two_level(
            inbound_station_counts
        ),
        "inbound_station_transition_by_od_district": normalize_three_level(
            inbound_station_by_od_counts
        ),
        "self_station_records": self_station_records,
        "missing_static_station_ids": sorted(missing_static_station_ids),
    }


def write_json(path: Path, data: Any) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def write_self_station_csv(path: Path, records: list[OdRecord]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(
            [
                "origin_station_id",
                "origin_station_name",
                "district",
                "transaction_count",
            ]
        )
        for record in sorted(
            records,
            key=lambda item: (
                item.origin_district,
                item.origin_station_id,
                item.origin_station_name,
            ),
        ):
            writer.writerow(
                [
                    record.origin_station_id,
                    record.origin_station_name,
                    record.origin_district,
                    record.count,
                ]
            )


def write_report(
    path: Path,
    profile_results: dict[str, dict[str, Any]],
    cleaning_metadata_by_profile: dict[str, dict[str, Any]] | None = None,
) -> None:
    cleaning_metadata_by_profile = cleaning_metadata_by_profile or {}
    lines = [
        "# OD Transition Matrix Build Report",
        "",
        "本報告由 `scripts/build_transition_matrices.py` 產生。",
        "",
        "## 同站借還處理策略",
        "",
        "起點站 = 終點站的 OD 已獨立抽出為 `*_self_station_records.csv`，",
        "不放入一般移動 transition matrix。這樣可以保留壞車立即歸還、取消租借、",
        "或真實來回旅次的資訊，同時避免一般 OD 被自我循環扭曲。",
        "",
        "## 輸出矩陣",
        "",
        "- `*_station_exit_transition_by_district.json`：每個行政區內，起點站選擇同區目的站或 `__OUT_OF_DISTRICT__`。",
        "- `*_inter_district_transition.json`：已離開原行政區後，Dummy Node 選擇目的行政區。",
        "- `*_inbound_station_transition_by_district.json`：進入某行政區後，選擇目的站點的整體分布。",
        "- `*_inbound_station_transition_by_od_district.json`：依 origin district 與 destination district 保留更細的入站分布。",
        "- `*_self_station_records.csv`：起終點同站交易，供後續獨立建模。",
        "",
        "## Profile 摘要",
        "",
    ]

    for profile, result in sorted(profile_results.items()):
        meta = result["metadata"]
        cleaning = cleaning_metadata_by_profile.get(profile, {})
        lines.extend(
            [
                f"### {profile}",
                "",
                f"- 總交易數：{meta['total_transactions']}",
                f"- 同站借還交易數：{meta['self_station_transactions']}",
                f"- 同站借還比例：{meta['self_station_ratio']:.4f}",
                f"- 跨行政區交易數：{meta['cross_district_transactions']}",
                f"- 排除同站後跨行政區比例：{meta['cross_district_ratio_excluding_self']:.4f}",
                f"- 同行政區且非同站交易數：{meta['same_district_moving_transactions']}",
                f"- OD 內站點數：{meta['station_count_in_od']}",
                f"- OD 內行政區數：{meta['district_count_in_od']}",
                f"- 未在 static station 檔找到的 station id 數：{meta['missing_static_station_id_count']}",
                f"- 因排除站點略過交易數：{cleaning.get('skipped_transactions_by_excluded_station', 0)}",
                f"- 因不在 valid station registry 略過交易數：{cleaning.get('skipped_transactions_by_invalid_station', 0)}",
                "",
            ]
        )

    aliases = next(
        (
            cleaning.get("district_aliases", {})
            for cleaning in cleaning_metadata_by_profile.values()
            if cleaning.get("district_aliases")
        ),
        {},
    )
    excluded = next(
        (
            cleaning.get("excluded_station_ids", [])
            for cleaning in cleaning_metadata_by_profile.values()
            if cleaning.get("excluded_station_ids")
        ),
        [],
    )
    if aliases or excluded:
        lines.extend(["## 清理規則", ""])
        if aliases:
            lines.append("行政區別名：")
            lines.append("")
            for source, target in aliases.items():
                lines.append(f"- {source} -> {target}")
            lines.append("")
        if excluded:
            lines.append("排除站點：")
            lines.append("")
            for station_id in excluded:
                lines.append(f"- {station_id}")
            lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def build_transition_matrices(
    input_dir: Path,
    output_dir: Path,
    cleaning_rules_json: Path | None = None,
    valid_stations_csv: Path | None = None,
) -> dict[str, dict[str, Any]]:
    static_stations = load_static_station_info(input_dir / "youbike_static_info.csv")
    cleaning_rules = load_cleaning_rules(cleaning_rules_json)
    valid_station_ids = load_valid_station_ids(valid_stations_csv)
    geojson_paths = sorted(input_dir.glob("*.geojson"))
    if not geojson_paths:
        raise FileNotFoundError(f"找不到 GeoJSON 檔案：{input_dir}")

    output_dir.mkdir(parents=True, exist_ok=True)
    results: dict[str, dict[str, Any]] = {}
    cleaning_metadata_by_profile: dict[str, dict[str, Any]] = {}

    for geojson_path in geojson_paths:
        profile = infer_profile(geojson_path)
        records, cleaning_metadata = load_od_records(
            geojson_path,
            district_aliases=cleaning_rules["district_aliases"],
            excluded_station_ids=set(cleaning_rules["excluded_station_ids"]),
            valid_station_ids=valid_station_ids,
        )
        result = build_profile_matrices(profile, records, static_stations)
        results[profile] = result
        cleaning_metadata_by_profile[profile] = cleaning_metadata

        write_json(
            output_dir / f"{profile}_station_to_district.json",
            result["station_to_district"],
        )
        write_json(
            output_dir / f"{profile}_station_exit_transition_by_district.json",
            result["station_exit_transition_by_district"],
        )
        write_json(
            output_dir / f"{profile}_inter_district_transition.json",
            result["inter_district_transition"],
        )
        write_json(
            output_dir / f"{profile}_inbound_station_transition_by_district.json",
            result["inbound_station_transition_by_district"],
        )
        write_json(
            output_dir / f"{profile}_inbound_station_transition_by_od_district.json",
            result["inbound_station_transition_by_od_district"],
        )
        write_json(
            output_dir / f"{profile}_build_metadata.json",
            result["metadata"],
        )
        write_json(
            output_dir / f"{profile}_missing_static_station_ids.json",
            result["missing_static_station_ids"],
        )
        write_self_station_csv(
            output_dir / f"{profile}_self_station_records.csv",
            result["self_station_records"],
        )

    write_report(
        output_dir / "matrix_build_report.md",
        results,
        cleaning_metadata_by_profile,
    )
    return results


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build district-based YouBike transition matrices from OD GeoJSON files."
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=Path("data/raw"),
        help="包含 OD GeoJSON 與 youbike_static_info.csv 的資料夾。",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/processed/transition_matrices"),
        help="transition matrix 輸出資料夾。",
    )
    parser.add_argument(
        "--cleaning-rules-json",
        type=Path,
        default=None,
        help="包含 district_aliases 與 excluded_station_ids 的 JSON 清理規則。",
    )
    parser.add_argument(
        "--valid-stations-csv",
        type=Path,
        default=None,
        help="只保留此 static station CSV 中存在的 station id。",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    results = build_transition_matrices(
        args.input_dir,
        args.output_dir,
        args.cleaning_rules_json,
        args.valid_stations_csv,
    )
    for profile, result in sorted(results.items()):
        meta = result["metadata"]
        print(
            f"{profile}: total={meta['total_transactions']}, "
            f"self={meta['self_station_transactions']} "
            f"({meta['self_station_ratio']:.2%}), "
            f"cross={meta['cross_district_transactions']}"
        )
    print(f"Transition matrices written to: {args.output_dir.resolve()}")


if __name__ == "__main__":
    main()
