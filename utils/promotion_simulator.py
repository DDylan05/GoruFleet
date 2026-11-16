import argparse
import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd


# utils/promotion_simulator.py에서 두 단계 위가 프로젝트 최상위 폴더
BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "outputs"
PROMOTION_OUTPUT_DIR_NAME = "promotion_senario_results"

STATION_PATH = DATA_DIR / "서울시 따릉이대여소 마스터 정보.csv"
CONFIG_DIR = BASE_DIR / "config"
PROMOTION_SETTINGS_PATH = CONFIG_DIR / "promotion_settings.json"


# 프로모션 기본 설정값
# 설정 파일이 없거나 최초 실행할 때 적용하는 초기 기본값이다.
DEFAULT_PROMOTION_SETTINGS: dict = {
    "stage_rules": {
        1: {"max_source_band": 2, "min_target_band": 9},
        2: {"max_source_band": 3, "min_target_band": 8},
        3: {"max_source_band": 4, "min_target_band": 7},
    },
    "participation_rates": {
        1: 0.03,
        2: 0.05,
        3: 0.10,
    },
    "min_distance_km": 1.0,
    "bonus_distance_threshold_km": 1.5,
    "max_distance_km": 2.0,
    "base_mileage_points": 200,
    "bonus_mileage_1_points": 50,
    "bonus_mileage_2_points": 100,
    "point_value_krw": 1.0,
}


def get_default_promotion_settings() -> dict:
    """기본 프로모션 설정값의 복사본을 반환한다."""
    return deepcopy(DEFAULT_PROMOTION_SETTINGS)


def load_current_promotion_settings(
    config_path: str | Path | None = None,
) -> dict:
    """config에 저장된 현재 프로모션 설정을 불러온다. 파일이 없으면 기본값을 반환한다."""
    path = Path(config_path) if config_path is not None else PROMOTION_SETTINGS_PATH

    if not path.exists():
        return get_default_promotion_settings()

    with path.open("r", encoding="utf-8") as file:
        saved_settings = json.load(file)

    # 설정 파일이 향후 메타데이터를 포함하는 구조로 바뀌어도 읽을 수 있도록 처리한다.
    if isinstance(saved_settings, dict) and "promotion_settings" in saved_settings:
        saved_settings = saved_settings["promotion_settings"]

    return resolve_promotion_settings(saved_settings)


def save_current_promotion_settings(
    settings: dict,
    config_path: str | Path | None = None,
) -> Path:
    """현재 시스템에서 사용할 프로모션 설정을 config에 원자적으로 저장한다."""
    path = Path(config_path) if config_path is not None else PROMOTION_SETTINGS_PATH
    resolved_settings = resolve_promotion_settings(settings)
    path.parent.mkdir(parents=True, exist_ok=True)

    temporary_path = path.with_suffix(path.suffix + ".tmp")
    with temporary_path.open("w", encoding="utf-8") as file:
        json.dump(resolved_settings, file, ensure_ascii=False, indent=2)

    temporary_path.replace(path)
    return path


def resolve_promotion_settings(settings: dict | None = None) -> dict:
    """기본 설정에 전달받은 설정을 반영하고 값의 유효성을 검사한다."""
    resolved = get_default_promotion_settings()

    if settings is not None:
        if not isinstance(settings, dict):
            raise TypeError("promotion_settings는 딕셔너리 형태여야 합니다.")

        for key, value in settings.items():
            if key not in resolved:
                raise ValueError(f"알 수 없는 프로모션 설정 항목입니다: {key}")

            if key in {"stage_rules", "participation_rates"}:
                if not isinstance(value, dict):
                    raise TypeError(f"{key} 설정은 딕셔너리 형태여야 합니다.")

                for stage, stage_value in value.items():
                    stage = int(stage)
                    if stage not in (1, 2, 3):
                        raise ValueError("프로모션 단계는 1, 2, 3만 설정할 수 있습니다.")

                    if key == "stage_rules":
                        if not isinstance(stage_value, dict):
                            raise TypeError("단계별 우선구간 설정은 딕셔너리 형태여야 합니다.")
                        resolved[key][stage].update(stage_value)
                    else:
                        resolved[key][stage] = float(stage_value)
            else:
                resolved[key] = value

    for stage, rule in resolved["stage_rules"].items():
        source_band = int(rule["max_source_band"])
        target_band = int(rule["min_target_band"])
        if not 1 <= source_band <= 10 or not 1 <= target_band <= 10:
            raise ValueError("공급·수요 우선구간은 1~10 사이여야 합니다.")
        rule["max_source_band"] = source_band
        rule["min_target_band"] = target_band

    for stage, rate in resolved["participation_rates"].items():
        rate = float(rate)
        if not 0 <= rate <= 1:
            raise ValueError(f"{stage}단계 참여율은 0~1 사이여야 합니다.")
        resolved["participation_rates"][stage] = rate

    min_distance = float(resolved["min_distance_km"])
    bonus_threshold = float(resolved["bonus_distance_threshold_km"])
    max_distance = float(resolved["max_distance_km"])

    if not 0 <= min_distance < bonus_threshold <= max_distance:
        raise ValueError(
            "거리 조건은 최소 거리 < 추가 보상 기준 거리 <= 최대 거리여야 합니다."
        )

    resolved["min_distance_km"] = min_distance
    resolved["bonus_distance_threshold_km"] = bonus_threshold
    resolved["max_distance_km"] = max_distance

    for key in (
        "base_mileage_points",
        "bonus_mileage_1_points",
        "bonus_mileage_2_points",
    ):
        value = int(resolved[key])
        if value < 0:
            raise ValueError(f"{key} 값은 음수일 수 없습니다.")
        resolved[key] = value

    point_value = float(resolved["point_value_krw"])
    if point_value < 0:
        raise ValueError("포인트 환산 가치는 음수일 수 없습니다.")
    resolved["point_value_krw"] = point_value

    return resolved


def save_promotion_settings(
    output_dir: str | Path,
    applied_settings: dict,
    random_state: int = 42,
) -> Path:
    """실행 당시 기본 설정과 실제 적용 설정을 JSON으로 저장한다."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    settings_history = {
        "schema_version": 1,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "random_state": int(random_state),
        "default_settings": get_default_promotion_settings(),
        "applied_settings": resolve_promotion_settings(applied_settings),
    }

    settings_path = output_dir / "promotion_settings.json"
    temporary_path = output_dir / "promotion_settings.json.tmp"

    with temporary_path.open("w", encoding="utf-8") as file:
        json.dump(settings_history, file, ensure_ascii=False, indent=2)

    temporary_path.replace(settings_path)
    return settings_path


def read_csv_auto(file_path: str | Path) -> pd.DataFrame:
    """CSV 파일을 UTF-8 계열 또는 CP949로 읽는다."""
    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError(f"파일을 찾을 수 없습니다: {path}")

    for encoding in ["utf-8-sig", "cp949", "utf-8"]:
        try:
            return pd.read_csv(path, encoding=encoding)
        except UnicodeDecodeError:
            continue

    raise UnicodeError(f"CSV 인코딩을 확인할 수 없습니다: {path}")


def calculate_distance_km(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
) -> float:
    """두 정류소 좌표 사이의 직선거리를 km로 계산한다."""
    lat1, lon1, lat2, lon2 = map(
        np.radians,
        [lat1, lon1, lat2, lon2],
    )

    dlat = lat2 - lat1
    dlon = lon2 - lon1

    a = (
        np.sin(dlat / 2) ** 2
        + np.cos(lat1)
        * np.cos(lat2)
        * np.sin(dlon / 2) ** 2
    )

    return float(6371 * 2 * np.arcsin(np.sqrt(np.clip(a, 0, 1))))


def build_distance_candidates(
    station_info: pd.DataFrame,
    min_distance_km: float = 1.0,
    max_distance_km: float = 2.0,
) -> pd.DataFrame:
    """거리 조건을 만족하는 정류소 쌍을 생성한다."""
    if min_distance_km < 0 or max_distance_km < min_distance_km:
        raise ValueError("거리 조건을 확인해 주세요.")

    required = ["대여소_ID", "위도", "경도"]
    missing = [col for col in required if col not in station_info.columns]

    if missing:
        raise ValueError(f"정류소 정보에 필요한 컬럼이 없습니다: {missing}")

    stations = station_info[required].copy()
    stations["대여소_ID"] = stations["대여소_ID"].astype(str).str.strip()
    stations["위도"] = pd.to_numeric(stations["위도"], errors="coerce")
    stations["경도"] = pd.to_numeric(stations["경도"], errors="coerce")

    stations = stations.dropna(subset=["대여소_ID", "위도", "경도"])
    stations = stations[
        (stations["위도"] != 0) & (stations["경도"] != 0)
    ]
    stations = stations.drop_duplicates("대여소_ID")

    rows = []
    station_list = stations.to_dict("records")

    for i in range(len(station_list)):
        source = station_list[i]

        for j in range(i + 1, len(station_list)):
            target = station_list[j]

            distance = calculate_distance_km(
                source["위도"],
                source["경도"],
                target["위도"],
                target["경도"],
            )

            if min_distance_km <= distance <= max_distance_km:
                rows.append({
                    "정류소_A": source["대여소_ID"],
                    "정류소_B": target["대여소_ID"],
                    "직선거리_km": round(distance, 3),
                })

    return pd.DataFrame(
        rows,
        columns=["정류소_A", "정류소_B", "직선거리_km"],
    )


def get_mileage_reward(
    distance_km: float,
    promotion_settings: dict | None = None,
) -> tuple[int, int, int]:
    """설정값에 따라 거리별 기본·추가·총 마일리지를 계산한다."""
    settings = resolve_promotion_settings(promotion_settings)
    min_distance = settings["min_distance_km"]
    bonus_threshold = settings["bonus_distance_threshold_km"]
    max_distance = settings["max_distance_km"]

    if min_distance <= distance_km < bonus_threshold:
        bonus_points = settings["bonus_mileage_1_points"]
    elif bonus_threshold <= distance_km <= max_distance:
        bonus_points = settings["bonus_mileage_2_points"]
    else:
        raise ValueError(f"보상 기준에 없는 거리입니다: {distance_km}km")

    base_points = settings["base_mileage_points"]
    return base_points, bonus_points, base_points + bonus_points


def assign_promotion_tiers(hour_df: pd.DataFrame) -> pd.DataFrame:
    """시간대별 공급·수요 우선구간을 계산한다."""
    df = hour_df.copy()

    stock = pd.to_numeric(df["종료재고_조치전"], errors="coerce")
    lower = pd.to_numeric(df["하한재고"], errors="coerce")
    upper = pd.to_numeric(df["상한재고"], errors="coerce")

    valid = lower.notna() & upper.notna()

    df["부족량_조치전"] = (lower - stock).clip(lower=0)
    df["초과량_조치전"] = (stock - upper).clip(lower=0)

    df["운영상태_조치전"] = "범위 산출 불가"
    df.loc[valid, "운영상태_조치전"] = "적정"
    df.loc[
        valid & (stock < lower),
        "운영상태_조치전",
    ] = "공급부족"
    df.loc[
        valid & (stock > upper),
        "운영상태_조치전",
    ] = "공급과잉"

    df["공급우선구간"] = pd.Series(
        pd.NA,
        index=df.index,
        dtype="Int64",
    )
    df["수요우선구간"] = pd.Series(
        pd.NA,
        index=df.index,
        dtype="Int64",
    )

    source_mask = valid & (df["초과량_조치전"] > 0)

    if source_mask.any():
        source_pct = df.loc[
            source_mask,
            "초과량_조치전",
        ].rank(
            method="average",
            ascending=True,
            pct=True,
        )

        source_bands = (
            np.floor((1 - source_pct) * 10) + 1
        ).clip(1, 10).astype(int)

        df.loc[source_mask, "공급우선구간"] = source_bands

    target_mask = valid & (df["부족량_조치전"] > 0)

    if target_mask.any():
        target_pct = df.loc[
            target_mask,
            "부족량_조치전",
        ].rank(
            method="average",
            ascending=True,
            pct=True,
        )

        target_bands = (
            np.ceil(target_pct * 10)
        ).clip(1, 10).astype(int)

        df.loc[target_mask, "수요우선구간"] = target_bands

    return df


def simulate_promotion_stage(
    operations_result: pd.DataFrame,
    distance_candidates: pd.DataFrame,
    stage: int = 1,
    promotion_settings: dict | None = None,
    participation_rate: float | None = None,
    random_state: int = 42,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    운영 결과를 기준으로 하나의 프로모션 단계를 시뮬레이션한다.

    단계별 공급·수요 우선구간과 참여율은 promotion_settings에서 가져온다.

    반환:
    - scenario_result: 시간대·정류소별 프로모션 전후 결과
    - transfer_plan: 정류소 간 이동 계획
    """
    required = [
        "기준_날짜",
        "기준_시간대",
        "대여소_ID",
        "시작재고",
        "대여수요_정수",
        "반납량_정수",
        "하한재고",
        "상한재고",
    ]

    missing = [
        col for col in required
        if col not in operations_result.columns
    ]

    if missing:
        raise ValueError(
            f"운영 결과에 필요한 컬럼이 없습니다: {missing}"
        )

    settings = resolve_promotion_settings(promotion_settings)
    stage_rules = settings["stage_rules"]
    participation_rates = settings["participation_rates"]
    point_value_krw = settings["point_value_krw"]

    if stage not in stage_rules:
        raise ValueError("프로모션 단계는 1, 2, 3 중 하나여야 합니다.")

    if participation_rate is None:
        participation_rate = participation_rates[stage]

    if not 0 <= participation_rate <= 1:
        raise ValueError("참여율은 0~1 사이여야 합니다.")

    distance_required = {
        "정류소_A",
        "정류소_B",
        "직선거리_km",
    }

    if not distance_required.issubset(distance_candidates.columns):
        raise ValueError(
            "distance_candidates의 컬럼을 확인해 주세요."
        )

    rng = np.random.default_rng(random_state + stage)

    allowed_pairs = set()
    distance_map = {}

    for row in distance_candidates.to_dict("records"):
        station_a = str(row["정류소_A"]).strip()
        station_b = str(row["정류소_B"]).strip()

        if station_a == station_b:
            continue

        pair = frozenset((station_a, station_b))
        allowed_pairs.add(pair)
        distance_map[pair] = float(row["직선거리_km"])

    df = operations_result.copy()

    df["기준_날짜"] = pd.to_datetime(
        df["기준_날짜"],
        errors="raise",
    ).dt.normalize()

    df["대여소_ID"] = (
        df["대여소_ID"]
        .astype(str)
        .str.strip()
    )

    df["기준_시간대"] = pd.to_numeric(
        df["기준_시간대"],
        errors="raise",
    ).astype(int)

    numeric_cols = [
        "시작재고",
        "대여수요_정수",
        "반납량_정수",
    ]

    for col in numeric_cols:
        df[col] = pd.to_numeric(
            df[col],
            errors="coerce",
        )

    if df[numeric_cols].isna().any().any():
        raise ValueError(
            "시작재고·대여수요·반납량에 결측값이 있습니다."
        )

    if (df[numeric_cols] < 0).any().any():
        raise ValueError(
            "시작재고·대여수요·반납량은 음수일 수 없습니다."
        )

    df["시뮬레이션_시각"] = (
        df["기준_날짜"]
        + pd.to_timedelta(df["기준_시간대"], unit="h")
    )

    df = df.sort_values(
        ["시뮬레이션_시각", "대여소_ID"]
    ).reset_index(drop=True)

    if df.duplicated(
        ["시뮬레이션_시각", "대여소_ID"]
    ).any():
        raise ValueError(
            "같은 시각·대여소 조합이 중복되어 있습니다."
        )

    first_rows = df.drop_duplicates(
        "대여소_ID",
        keep="first",
    )

    stock_by_station = (
        first_rows
        .set_index("대여소_ID")["시작재고"]
        .astype(int)
        .to_dict()
    )

    max_source_band = stage_rules[stage]["max_source_band"]
    min_target_band = stage_rules[stage]["min_target_band"]

    transfer_rows = []
    result_rows = []

    for timestamp, hour_data in df.groupby(
        "시뮬레이션_시각",
        sort=True,
    ):
        hour_rows = []

        # 1. 대여·반납 수요를 반영한 프로모션 적용 전 재고 계산
        for row in hour_data.to_dict("records"):
            station = row["대여소_ID"]
            start_stock = stock_by_station[station]

            demand = int(row["대여수요_정수"])
            returns = int(row["반납량_정수"])

            expected_served_rental = min(
                demand,
                start_stock,
            )

            row["시작재고_시나리오"] = start_stock
            row["예상_충족대여량_시나리오"] = expected_served_rental
            row["예상_미충족대여수요_시나리오"] = (
                demand - expected_served_rental
            )

            row["종료재고_조치전"] = (
                start_stock
                - expected_served_rental
                + returns
            )

            hour_rows.append(row)

        hour_df = assign_promotion_tiers(
            pd.DataFrame(hour_rows)
        )

        hour_df["프로모션단계"] = stage
        hour_df["참여율_가정"] = participation_rate

        stock_after = (
            hour_df
            .set_index("대여소_ID")["종료재고_조치전"]
            .astype(int)
            .to_dict()
        )

        lower = (
            hour_df
            .set_index("대여소_ID")["하한재고"]
            .to_dict()
        )
        upper = (
            hour_df
            .set_index("대여소_ID")["상한재고"]
            .to_dict()
        )
        source_tiers = (
            hour_df
            .set_index("대여소_ID")["공급우선구간"]
            .to_dict()
        )
        target_tiers = (
            hour_df
            .set_index("대여소_ID")["수요우선구간"]
            .to_dict()
        )

        # 2. 선택 단계의 공급처 선정
        source_mask = (
            (hour_df["운영상태_조치전"] == "공급과잉")
            & (hour_df["공급우선구간"] <= max_source_band)
        ).fillna(False)

        sources = (
            hour_df.loc[source_mask]
            .sort_values(
                "초과량_조치전",
                ascending=False,
            )["대여소_ID"]
            .tolist()
        )

        # 3. 선택 단계의 수요처 선정
        target_mask = (
            (hour_df["운영상태_조치전"] == "공급부족")
            & (hour_df["수요우선구간"] >= min_target_band)
        ).fillna(False)

        targets = (
            hour_df.loc[target_mask]
            .sort_values(
                "부족량_조치전",
                ascending=False,
            )["대여소_ID"]
            .tolist()
        )

        # 4. 참여율 적용 전 100% 계획 이동량 계산
        planned_stock = stock_after.copy()
        planned_pairs = []

        for source in sources:
            for target in targets:
                if source == target:
                    continue

                pair = frozenset((source, target))

                if pair not in allowed_pairs:
                    continue

                distance_km = distance_map[pair]

                excess = max(
                    0,
                    planned_stock[source] - upper[source],
                )
                shortage = max(
                    0,
                    lower[target] - planned_stock[target],
                )

                planned_move = int(
                    min(excess, shortage)
                )

                if planned_move <= 0:
                    continue

                planned_pairs.append({
                    "source": source,
                    "target": target,
                    "distance_km": distance_km,
                    "planned_move": planned_move,
                })

                planned_stock[source] -= planned_move
                planned_stock[target] += planned_move

        # 5. 참여율을 적용한 기대 이동량·모의 이동량 계산
        for match in planned_pairs:
            source = match["source"]
            target = match["target"]
            distance_km = match["distance_km"]
            planned_move = match["planned_move"]

            simulated_move = int(
                rng.binomial(
                    planned_move,
                    participation_rate,
                )
            )

            source_before = stock_after[source]
            target_before = stock_after[target]

            stock_after[source] -= simulated_move
            stock_after[target] += simulated_move

            base_points, bonus_points, total_points = (
                get_mileage_reward(distance_km, settings)
            )

            expected_moves = (
                planned_move * participation_rate
            )

            expected_points = (
                expected_moves * total_points
            )
            expected_cost = (
                expected_points * point_value_krw
            )

            simulated_points = (
                simulated_move * total_points
            )
            simulated_cost = (
                simulated_points * point_value_krw
            )

            transfer_rows.append({
                "기준시각": timestamp,
                "단계": stage,
                "참여율_가정": participation_rate,
                "공급정류소": source,
                "수요정류소": target,
                "공급우선구간": source_tiers[source],
                "수요우선구간": target_tiers[target],
                "직선거리_km": round(distance_km, 3),
                "계획이동량": planned_move,
                "기대이동량": expected_moves,
                "가상이동량": simulated_move,
                "기본마일리지_포인트": base_points,
                "추가마일리지_포인트": bonus_points,
                "총마일리지_대당": total_points,
                "기대마일리지_포인트": expected_points,
                "기대마일리지_원": expected_cost,
                "시뮬레이션_마일리지_포인트": simulated_points,
                "시뮬레이션_마일리지_원": simulated_cost,
                "공급재고_이동전": source_before,
                "공급재고_이동후": stock_after[source],
                "수요재고_이동전": target_before,
                "수요재고_이동후": stock_after[target],
            })

        # 6. 프로모션 적용 후 상태 재판정
        hour_df["종료재고_조치후"] = (
            hour_df["대여소_ID"].map(stock_after)
        )

        final_stock = hour_df["종료재고_조치후"]
        valid = (
            hour_df["하한재고"].notna()
            & hour_df["상한재고"].notna()
        )

        hour_df["부족량_조치후"] = (
            hour_df["하한재고"] - final_stock
        ).clip(lower=0)

        hour_df["초과량_조치후"] = (
            final_stock - hour_df["상한재고"]
        ).clip(lower=0)

        hour_df["운영상태_조치후"] = "범위 산출 불가"
        hour_df.loc[valid, "운영상태_조치후"] = "적정"
        hour_df.loc[
            valid
            & (final_stock < hour_df["하한재고"]),
            "운영상태_조치후",
        ] = "공급부족"
        hour_df.loc[
            valid
            & (final_stock > hour_df["상한재고"]),
            "운영상태_조치후",
        ] = "공급과잉"

        result_rows.append(hour_df)

        # 프로모션 후 재고를 다음 시간대에 전달
        stock_by_station.update(stock_after)

    scenario_result = pd.concat(
        result_rows,
        ignore_index=True,
    )

    transfer_columns = [
        "기준시각",
        "단계",
        "참여율_가정",
        "공급정류소",
        "수요정류소",
        "공급우선구간",
        "수요우선구간",
        "직선거리_km",
        "계획이동량",
        "기대이동량",
        "가상이동량",
        "기본마일리지_포인트",
        "추가마일리지_포인트",
        "총마일리지_대당",
        "기대마일리지_포인트",
        "기대마일리지_원",
        "시뮬레이션_마일리지_포인트",
        "시뮬레이션_마일리지_원",
        "공급재고_이동전",
        "공급재고_이동후",
        "수요재고_이동전",
        "수요재고_이동후",
    ]

    transfer_plan = pd.DataFrame(
        transfer_rows,
        columns=transfer_columns,
    )

    scenario_result = scenario_result.sort_values(
        ["기준_날짜", "기준_시간대", "대여소_ID"]
    ).reset_index(drop=True)

    return scenario_result, transfer_plan


def calculate_baseline(
    operations_result: pd.DataFrame,
) -> tuple[float, float, int]:
    """프로모션 미적용 기준의 부족량·초과량·적정 조합 수를 계산한다."""
    required = ["종료재고", "하한재고", "상한재고"]
    missing = [
        col for col in required
        if col not in operations_result.columns
    ]

    if missing:
        raise ValueError(
            f"기준 결과에 필요한 컬럼이 없습니다: {missing}"
        )

    stock = pd.to_numeric(
        operations_result["종료재고"],
        errors="coerce",
    )
    lower = pd.to_numeric(
        operations_result["하한재고"],
        errors="coerce",
    )
    upper = pd.to_numeric(
        operations_result["상한재고"],
        errors="coerce",
    )

    valid = lower.notna() & upper.notna()

    shortage = (
        (lower - stock)
        .clip(lower=0)
        .loc[valid]
        .sum()
    )

    surplus = (
        (stock - upper)
        .clip(lower=0)
        .loc[valid]
        .sum()
    )

    ok = (
        (stock >= lower)
        & (stock <= upper)
        & valid
    ).sum()

    return float(shortage), float(surplus), int(ok)


def build_promotion_comparison(
    operations_result: pd.DataFrame,
    stage_results: dict[int, tuple[pd.DataFrame, pd.DataFrame]],
    participation_rates: dict[int, float] | None = None,
) -> pd.DataFrame:
    """1~3단계 프로모션 결과를 비교 요약한다."""
    if participation_rates is None:
        participation_rates = resolve_promotion_settings()["participation_rates"]

    baseline_shortage, baseline_surplus, baseline_ok = (
        calculate_baseline(operations_result)
    )

    rows = [{
        "시나리오": "프로모션 미적용",
        "참여율(%)": 0,
        "계획연결건수": 0,
        "실제이동연결건수": 0,
        "계획이동량": 0,
        "기대이동량": 0,
        "모의이동량": 0,
        "기대마일리지_원": 0,
        "모의마일리지_원": 0,
        "기대비용_기대이동량당_원": 0,
        "모의비용_모의이동량당_원": 0,
        "부족량_기준": baseline_shortage,
        "부족량_조치전_시간대합": baseline_shortage,
        "부족량_조치후": baseline_shortage,
        "부족량_감소량_기준대비": 0,
        "부족량_감소율_기준대비(%)": 0,
        "초과량_기준": baseline_surplus,
        "초과량_조치전_시간대합": baseline_surplus,
        "초과량_조치후": baseline_surplus,
        "초과량_감소량_기준대비": 0,
        "초과량_감소율_기준대비(%)": 0,
        "적정범위조합_기준": baseline_ok,
        "적정범위조합_조치전_시간대합": baseline_ok,
        "적정범위조합_조치후": baseline_ok,
        "적정범위조합_증감_기준대비": 0,
    }]

    for stage in [1, 2, 3]:
        result, plan = stage_results[stage]

        valid = (
            result["하한재고"].notna()
            & result["상한재고"].notna()
        )

        before_shortage = (
            result.loc[valid, "부족량_조치전"].sum()
        )
        before_surplus = (
            result.loc[valid, "초과량_조치전"].sum()
        )
        before_ok = (
            result.loc[
                valid,
                "운영상태_조치전",
            ] == "적정"
        ).sum()

        after_shortage = (
            result.loc[valid, "부족량_조치후"].sum()
        )
        after_surplus = (
            result.loc[valid, "초과량_조치후"].sum()
        )
        after_ok = (
            result.loc[
                valid,
                "운영상태_조치후",
            ] == "적정"
        ).sum()

        planned_moves = (
            plan["계획이동량"].sum()
            if not plan.empty else 0
        )
        expected_moves = (
            plan["기대이동량"].sum()
            if not plan.empty else 0
        )
        simulated_moves = (
            plan["가상이동량"].sum()
            if not plan.empty else 0
        )
        expected_cost = (
            plan["기대마일리지_원"].sum()
            if not plan.empty else 0
        )
        simulated_cost = (
            plan["시뮬레이션_마일리지_원"].sum()
            if not plan.empty else 0
        )

        planned_pairs = len(plan)

        actual_transfer_count = (
            int((plan["가상이동량"] > 0).sum())
            if not plan.empty else 0
        )

        rows.append({
            "시나리오": f"프로모션 {stage}단계",
            "참여율(%)": participation_rates[stage] * 100,
            "계획연결건수": planned_pairs,
            "실제이동연결건수": actual_transfer_count,
            "계획이동량": planned_moves,
            "기대이동량": expected_moves,
            "모의이동량": simulated_moves,
            "기대마일리지_원": expected_cost,
            "모의마일리지_원": simulated_cost,
            "기대비용_기대이동량당_원": (
                expected_cost / expected_moves
                if expected_moves > 0 else 0
            ),
            "모의비용_모의이동량당_원": (
                simulated_cost / simulated_moves
                if simulated_moves > 0 else 0
            ),
            "부족량_기준": baseline_shortage,
            "부족량_조치전_시간대합": before_shortage,
            "부족량_조치후": after_shortage,
            "부족량_감소량_기준대비": (
                baseline_shortage - after_shortage
            ),
            "부족량_감소율_기준대비(%)": (
                (baseline_shortage - after_shortage)
                / baseline_shortage
                * 100
                if baseline_shortage > 0 else 0
            ),
            "초과량_기준": baseline_surplus,
            "초과량_조치전_시간대합": before_surplus,
            "초과량_조치후": after_surplus,
            "초과량_감소량_기준대비": (
                baseline_surplus - after_surplus
            ),
            "초과량_감소율_기준대비(%)": (
                (baseline_surplus - after_surplus)
                / baseline_surplus
                * 100
                if baseline_surplus > 0 else 0
            ),
            "적정범위조합_기준": baseline_ok,
            "적정범위조합_조치전_시간대합": before_ok,
            "적정범위조합_조치후": after_ok,
            "적정범위조합_증감_기준대비": (
                after_ok - baseline_ok
            ),
        })

    return pd.DataFrame(rows)


def get_promotion_output_dir(
    operations_result: pd.DataFrame,
    output_root: str | Path = OUTPUT_DIR,
) -> Path:
    """운영 결과의 기준 날짜를 확인해 날짜별 프로모션 결과 폴더를 반환한다."""
    if "기준_날짜" not in operations_result.columns:
        raise ValueError("운영 결과에 기준_날짜 컬럼이 없습니다.")

    dates = pd.to_datetime(
        operations_result["기준_날짜"],
        errors="coerce",
    )

    if dates.isna().any():
        raise ValueError("운영 결과의 기준_날짜에 비어 있거나 잘못된 값이 있습니다.")

    unique_dates = dates.dt.strftime("%Y%m%d").unique()

    if len(unique_dates) == 0:
        raise ValueError("프로모션 결과를 저장할 기준 날짜가 없습니다.")

    if len(unique_dates) > 1:
        date_list = ", ".join(sorted(unique_dates))
        raise ValueError(
            "한 번의 프로모션 실행에는 하나의 기준 날짜 데이터만 사용할 수 있습니다. "
            f"입력된 날짜: {date_list}"
        )

    return (
        Path(output_root)
        / PROMOTION_OUTPUT_DIR_NAME
        / unique_dates[0]
    )


def run_all_promotion_stages(
    operations_result: pd.DataFrame,
    station_info: pd.DataFrame,
    promotion_settings: dict | None = None,
    random_state: int = 42,
    output_dir: str | Path | None = None,
) -> tuple[
    dict[int, tuple[pd.DataFrame, pd.DataFrame]],
    pd.DataFrame,
]:
    """1~3단계 프로모션을 실행하고 기준 날짜별 폴더에 결과를 저장한다."""
    if output_dir is None:
        output_dir = get_promotion_output_dir(operations_result)
    else:
        output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    settings = resolve_promotion_settings(promotion_settings)

    distance_candidates = build_distance_candidates(
        station_info,
        min_distance_km=settings["min_distance_km"],
        max_distance_km=settings["max_distance_km"],
    )

    stage_results = {}

    for stage in [1, 2, 3]:
        result, plan = simulate_promotion_stage(
            operations_result=operations_result,
            distance_candidates=distance_candidates,
            stage=stage,
            promotion_settings=settings,
            random_state=random_state,
        )

        stage_results[stage] = (result, plan)

        result.to_csv(
            output_dir / f"promotion_result_stage{stage}.csv",
            index=False,
            encoding="utf-8-sig",
        )

        plan.to_csv(
            output_dir / f"promotion_transfer_stage{stage}.csv",
            index=False,
            encoding="utf-8-sig",
        )

    comparison = build_promotion_comparison(
        operations_result,
        stage_results,
        participation_rates=settings["participation_rates"],
    )

    comparison.to_csv(
        output_dir / "promotion_participation_cost_comparison.csv",
        index=False,
        encoding="utf-8-sig",
    )

    save_promotion_settings(
        output_dir=output_dir,
        applied_settings=settings,
        random_state=random_state,
    )

    return stage_results, comparison


def load_latest_operations_result(
    output_dir: str | Path = OUTPUT_DIR,
) -> pd.DataFrame:
    """가장 최근 생성된 운영 결과 CSV를 읽는다."""
    output_dir = Path(output_dir)
    result_dir = output_dir / "operation_results"

    candidates = list(
        result_dir.glob("operations_result_*.csv")
    )

    if not candidates:
        raise FileNotFoundError(
            f"운영 결과 파일을 찾을 수 없습니다: {result_dir}"
        )

    latest = max(
        candidates,
        key=lambda path: path.stat().st_mtime,
    )

    return read_csv_auto(latest)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="운영 결과를 기반으로 1~3단계 프로모션 시뮬레이션 실행"
    )
    parser.add_argument(
        "--operations-result",
        default=None,
        help="운영 결과 CSV 경로. 생략하면 가장 최근 파일을 사용합니다.",
    )
    parser.add_argument(
        "--station-master",
        default=str(STATION_PATH),
        help="정류소 마스터 CSV 경로",
    )
    parser.add_argument(
        "--min-distance",
        type=float,
        default=None,
        help="최소 직선거리(km)",
    )
    parser.add_argument(
        "--bonus-distance-threshold",
        type=float,
        default=None,
        help="2차 추가 보상 적용 거리(km)",
    )
    parser.add_argument(
        "--max-distance",
        type=float,
        default=None,
        help="최대 직선거리(km)",
    )
    parser.add_argument(
        "--base-mileage",
        type=int,
        default=None,
        help="기본 마일리지 포인트",
    )
    parser.add_argument(
        "--bonus-mileage-1",
        type=int,
        default=None,
        help="1차 추가 마일리지 포인트",
    )
    parser.add_argument(
        "--bonus-mileage-2",
        type=int,
        default=None,
        help="2차 추가 마일리지 포인트",
    )
    parser.add_argument(
        "--stage1-participation",
        type=float,
        default=None,
        help="1단계 참여율(0~1)",
    )
    parser.add_argument(
        "--stage2-participation",
        type=float,
        default=None,
        help="2단계 참여율(0~1)",
    )
    parser.add_argument(
        "--stage3-participation",
        type=float,
        default=None,
        help="3단계 참여율(0~1)",
    )
    parser.add_argument(
        "--point-value",
        type=float,
        default=None,
        help="1포인트의 원화 환산 가치",
    )
    parser.add_argument(
        "--random-state",
        type=int,
        default=42,
        help="모의 이동량 재현을 위한 난수 시드",
    )
    args = parser.parse_args()

    if args.operations_result:
        operations_path = Path(args.operations_result)
        if not operations_path.is_absolute():
            operations_path = BASE_DIR / operations_path

        operations_result = read_csv_auto(operations_path)
    else:
        operations_result = load_latest_operations_result()

    station_path = Path(args.station_master)
    if not station_path.is_absolute():
        station_path = BASE_DIR / station_path

    station_info = read_csv_auto(station_path)

    # 시스템 설정 파일의 값을 기본 적용값으로 불러온다.
    promotion_settings = load_current_promotion_settings()

    cli_setting_map = {
        "min_distance_km": args.min_distance,
        "bonus_distance_threshold_km": args.bonus_distance_threshold,
        "max_distance_km": args.max_distance,
        "base_mileage_points": args.base_mileage,
        "bonus_mileage_1_points": args.bonus_mileage_1,
        "bonus_mileage_2_points": args.bonus_mileage_2,
        "point_value_krw": args.point_value,
    }

    for key, value in cli_setting_map.items():
        if value is not None:
            promotion_settings[key] = value

    participation_args = {
        1: args.stage1_participation,
        2: args.stage2_participation,
        3: args.stage3_participation,
    }

    for stage, rate in participation_args.items():
        if rate is not None:
            promotion_settings["participation_rates"][stage] = rate

    promotion_settings = resolve_promotion_settings(promotion_settings)

    stage_results, comparison = run_all_promotion_stages(
        operations_result=operations_result,
        station_info=station_info,
        promotion_settings=promotion_settings,
        random_state=args.random_state,
    )

    distance_candidates = build_distance_candidates(
        station_info,
        min_distance_km=promotion_settings["min_distance_km"],
        max_distance_km=promotion_settings["max_distance_km"],
    )

    print(f"거리 후보 정류소 쌍: {len(distance_candidates)}")

    for stage in [1, 2, 3]:
        _, plan = stage_results[stage]

        print(
            f"{stage}단계 완료: "
            f"계획 이동량 = {int(plan['계획이동량'].sum())}, "
            f"모의 이동량 = {int(plan['가상이동량'].sum())}, "
            f"마일리지 비용 = "
            f"{int(plan['시뮬레이션_마일리지_원'].sum())}"
        )

    print("\n프로모션 비교 결과")
    print(comparison.T)
    print(f"\n결과 저장 위치: {get_promotion_output_dir(operations_result)}")


if __name__ == "__main__":
    main()
