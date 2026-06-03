"""模型輸入檔讀取工具。

這個模組放的是「把已處理資料轉成 simulation code 可直接使用格式」的小工具。
目前先支援站點 hourly arrival rate。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping


def load_hourly_lambda_by_station(path: str | Path) -> dict[str, dict[int, float]]:
    """讀取站點 hourly lambda JSON。

    `scripts/build_arrival_rate_inputs.py` 輸出的 JSON 格式為：

    ```python
    {
        "500101001": {"0": 5.8, "1": 5.8, ...},
        ...
    }
    ```

    JSON 的 key 只能保證是字串，但 `demand_generator` 期待 hour key 是 int；
    因此這裡會把 `"0"` 轉成 `0`，讓回傳值可以直接傳入 generator。
    """

    raw_data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {
        str(station_id): {
            int(hour): float(lambda_value)
            for hour, lambda_value in hourly_lambda.items()
        }
        for station_id, hourly_lambda in raw_data.items()
    }


def get_station_hourly_lambda(
    hourly_lambda_by_station: Mapping[str, Mapping[int, float]],
    station_id: str,
) -> dict[int, float]:
    """取得單一站點的 hourly lambda。

    若站點不存在，明確丟出錯誤，避免模擬時安靜地把需求設成 0。
    """

    normalized_station_id = str(station_id)
    if normalized_station_id not in hourly_lambda_by_station:
        raise KeyError(f"找不到站點 {normalized_station_id} 的 hourly lambda。")
    return {
        int(hour): float(lambda_value)
        for hour, lambda_value in hourly_lambda_by_station[
            normalized_station_id
        ].items()
    }
