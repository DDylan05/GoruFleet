from __future__ import annotations

import json
import math
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
CONFIG_DIR = BASE_DIR / "config"
RELOCATION_SETTINGS_PATH = CONFIG_DIR / "relocation_settings.json"

# 초기 운영 기본값. 차량 대수는 설정 항목이 아니며 항상 2대로 유지한다.
DEFAULT_RELOCATION_SETTINGS = {
    "max_relocation_distance_km": 2.0,
    "min_transfer_quantity": 1,
    "max_work_bundles": 18,
}


def resolve_relocation_settings(settings: dict | None = None) -> dict:
    """입력 설정을 기본값과 병합하고 값의 범위와 자료형을 검증한다."""
    resolved = DEFAULT_RELOCATION_SETTINGS.copy()
    if settings is not None:
        if not isinstance(settings, dict):
            raise TypeError("재배치 설정은 딕셔너리 형태여야 합니다.")
        resolved.update(settings)

    try:
        max_distance = float(resolved["max_relocation_distance_km"])
        min_quantity_raw = float(resolved["min_transfer_quantity"])
        max_bundles_raw = float(resolved["max_work_bundles"])
    except (TypeError, ValueError, KeyError) as error:
        raise ValueError("재배치 설정값의 형식이 올바르지 않습니다.") from error

    if not math.isfinite(max_distance) or not 0.1 <= max_distance <= 20:
        raise ValueError("최대 이동 거리는 0.1~20km 범위로 입력해야 합니다.")
    if not math.isfinite(min_quantity_raw) or not min_quantity_raw.is_integer():
        raise ValueError("최소 이동 수량은 정수로 입력해야 합니다.")
    if not 1 <= min_quantity_raw <= 100:
        raise ValueError("최소 이동 수량은 1~100대 범위로 입력해야 합니다.")
    if not math.isfinite(max_bundles_raw) or not max_bundles_raw.is_integer():
        raise ValueError("최대 작업 묶음 수는 정수로 입력해야 합니다.")
    if not 1 <= max_bundles_raw <= 500:
        raise ValueError("최대 작업 묶음 수는 1~500건 범위로 입력해야 합니다.")

    return {
        "max_relocation_distance_km": max_distance,
        "min_transfer_quantity": int(min_quantity_raw),
        "max_work_bundles": int(max_bundles_raw),
    }


def load_relocation_settings() -> dict:
    """현재 저장된 재배치 설정을 읽는다. 파일이 없으면 기본값을 반환한다."""
    if not RELOCATION_SETTINGS_PATH.exists():
        return DEFAULT_RELOCATION_SETTINGS.copy()

    with open(RELOCATION_SETTINGS_PATH, "r", encoding="utf-8") as file:
        settings = json.load(file)
    return resolve_relocation_settings(settings)


def save_relocation_settings(settings: dict) -> dict:
    """검증된 설정을 임시 파일에 기록한 뒤 원자적으로 교체한다."""
    validated = resolve_relocation_settings(settings)
    RELOCATION_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = RELOCATION_SETTINGS_PATH.with_suffix(".json.tmp")

    with open(temporary_path, "w", encoding="utf-8") as file:
        json.dump(validated, file, ensure_ascii=False, indent=4)

    temporary_path.replace(RELOCATION_SETTINGS_PATH)
    return validated
