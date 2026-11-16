"""기준 시각별 실제 이용 실적과 잔여 운영 전망을 계산한다.

기존 일일 수요 예측, 원천 OD 데이터, 초기 재고 이력, 운영 결과의 적정 재고
범위를 재사용한다. 새로운 데이터 수집이나 예측 모델 학습은 수행하지 않는다.

사용 예시:
    result = run_rolling_operations(
        target_date="20261030",
        reference_hour=9,
        recent_window_hours=3,
    )

반환값:
    {
        "summary": dict,
        "station_result": DataFrame,  # 기준 시각 현재 정류소 상태
        "hourly_result": DataFrame,   # 기준 시각 이후 시간대별 예상 상태
        "actual_hourly": DataFrame,   # 24시간 실제/예측 대여·반납 비교
    }

데이터 범위:
    기존 EDA 및 모델링과 동일하게 출발·도착 정류소가 모두 강서구에
    속하는 정상 이용 기록만 사용한다.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from utils.daily_model_pipeline import (
    DATA_DIR,
    STATION_PATH,
    load_data,
    load_gangseo_station_ids,
)
from utils.operations_simulator import (
    HISTORY_DIR,
    OPERATIONS_DIR,
    PREDICTION_DIR,
    build_history_path,
    load_inventory_history,
    load_prediction_data,
    prepare_initial_inventory,
    read_csv_auto,
)

BASE_DIR = Path(__file__).resolve().parents[1]

ACTUAL_COLUMNS = [
    "기준_날짜", "대여소_ID", "기준_시간대",
    "대여실적", "반납실적", "대여예측건수", "반납예측건수",
    "대여차이", "반납차이",
]

BOUND_COLUMNS = [
    "기준_날짜", "대여소_ID", "기준_시간대",
    "하한재고", "상한재고", "범위상태",
]


def _parse_target_date(value: str | date | datetime | pd.Timestamp) -> pd.Timestamp:
    """YYYYMMDD 또는 날짜 객체를 날짜 기준 Timestamp로 변환한다."""
    if isinstance(value, str) and len(value) == 8 and value.isdigit():
        return pd.to_datetime(value, format="%Y%m%d").normalize()
    return pd.Timestamp(value).normalize()


def _resolve_prediction_path(
    target_day: pd.Timestamp,
    prediction_path: str | Path | None,
) -> Path:
    """선택한 날짜에 해당하는 예측 파일을 찾는다."""
    if prediction_path is not None:
        path = Path(prediction_path)
        if not path.is_absolute():
            path = BASE_DIR / path
        if not path.exists():
            raise FileNotFoundError(f"수요 예측 파일을 찾을 수 없습니다: {path}")
        return path

    date_label = target_day.strftime("%Y%m%d")
    candidates = [
        PREDICTION_DIR / f"demand_forecast_{date_label}.csv",
        BASE_DIR / "outputs" / f"demand_forecast_{date_label}.csv",
    ]
    for path in candidates:
        if path.exists():
            return path

    searched = "\n".join(str(path) for path in candidates)
    raise FileNotFoundError(
        f"{date_label} 날짜의 수요 예측 파일을 찾을 수 없습니다.\n{searched}"
    )


def _resolve_history_path(
    target_day: pd.Timestamp,
    history_path: str | Path | None,
) -> Path:
    """선택 날짜의 재고 이력 파일을 찾는다."""
    path = build_history_path(target_day, history_path)
    if not path.exists():
        raise FileNotFoundError(f"초기 재고 이력 파일을 찾을 수 없습니다: {path}")
    return path


def _resolve_operations_path(
    target_day: pd.Timestamp,
    operations_path: str | Path | None,
) -> Path:
    """선택 날짜의 운영 시뮬레이션 결과 파일을 찾는다."""
    if operations_path is None:
        path = OPERATIONS_DIR / f"operations_result_{target_day:%Y%m%d}.csv"
    else:
        path = Path(operations_path)
        if not path.is_absolute():
            path = BASE_DIR / path

    if not path.exists():
        raise FileNotFoundError(
            f"적정 재고 범위를 확인할 운영 결과 파일이 없습니다: {path}\n"
            "먼저 해당 날짜의 운영 시뮬레이션을 실행하세요."
        )
    return path


def _load_daily_actual_od(
    target_day: pd.Timestamp,
    data_dir: str | Path = DATA_DIR,
    station_path: str | Path = STATION_PATH,
) -> pd.DataFrame:
    """기존 일일 데이터 로더와 동일한 필터를 적용해 정상 OD 데이터를 읽는다.

    원천 파일이 존재하지만 기존 필터를 통과한 정상 기록이 하나도 없는 경우는
    해당 날짜의 강서구 내부 이동 실적이 0건인 것으로 처리한다. 원천 파일 자체가
    없거나 필수 컬럼이 잘못된 경우에는 오류를 발생시킨다.
    """
    date_label = target_day.strftime("%Y%m%d")
    source_path = Path(data_dir) / f"tpss_bcycl_od_statnhm_{date_label}.csv"
    if not source_path.exists():
        raise FileNotFoundError(f"선택 날짜의 원천 OD 파일이 없습니다: {source_path}")

    station_ids = load_gangseo_station_ids(station_path)
    try:
        normal_df, _ = load_data(
            start_date_str=date_label,
            end_date_str=date_label,
            folder_path=data_dir,
            station_path=station_path,
            station_ids=station_ids,
        )
    except ValueError as exc:
        # load_data는 정상 데이터가 0행이면 일반적인 학습 작업을 위해 예외를 낸다.
        # 이 모듈에서는 해당 날짜의 정상 강서구 내부 이동이 0건일 수 있으므로
        # 그 특정 상황만 빈 데이터로 전환한다. 다른 데이터 오류는 그대로 전달한다.
        if "최근 90일 정상 이용 데이터가 없습니다." not in str(exc):
            raise
        normal_df = pd.DataFrame(columns=[
            "기준_날짜", "기준_시간대", "시작_대여소_ID", "종료_대여소_ID", "집계_기준"
        ])

    return normal_df


def _build_actual_hourly(
    normal_df: pd.DataFrame,
    prediction_df: pd.DataFrame,
    target_day: pd.Timestamp,
) -> pd.DataFrame:
    """강서구 내부 이동 기준의 시간대별 실제 대여·반납량을 예측 결과와 결합한다."""
    station_ids = sorted(prediction_df["대여소_ID"].astype(str).unique().tolist())

    if normal_df.empty:
        rental_counts = pd.DataFrame(columns=["대여소_ID", "기준_시간대", "대여실적"])
        return_counts = pd.DataFrame(columns=["대여소_ID", "기준_시간대", "반납실적"])
    else:
        allowed_types = {"출발시간", "도착시간"}
        actual_types = set(normal_df["집계_기준"].dropna().astype(str).unique())
        unknown_types = sorted(actual_types - allowed_types)
        if unknown_types:
            raise ValueError(f"알 수 없는 집계_기준 값이 있습니다: {unknown_types}")

        rental_rows = normal_df[normal_df["집계_기준"].eq("출발시간")]
        return_rows = normal_df[normal_df["집계_기준"].eq("도착시간")]

        rental_counts = (
            rental_rows.groupby(["시작_대여소_ID", "기준_시간대"])
            .size()
            .rename("대여실적")
            .reset_index()
            .rename(columns={"시작_대여소_ID": "대여소_ID"})
        )
        return_counts = (
            return_rows.groupby(["종료_대여소_ID", "기준_시간대"])
            .size()
            .rename("반납실적")
            .reset_index()
            .rename(columns={"종료_대여소_ID": "대여소_ID"})
        )

    grid = pd.MultiIndex.from_product(
        [station_ids, range(24)], names=["대여소_ID", "기준_시간대"]
    ).to_frame(index=False)
    grid["기준_날짜"] = target_day

    result = grid.merge(rental_counts, on=["대여소_ID", "기준_시간대"], how="left")
    result = result.merge(return_counts, on=["대여소_ID", "기준_시간대"], how="left")
    result["대여실적"] = pd.to_numeric(result["대여실적"], errors="coerce").fillna(0).astype(int)
    result["반납실적"] = pd.to_numeric(result["반납실적"], errors="coerce").fillna(0).astype(int)

    prediction = prediction_df[[
        "대여소_ID", "기준_시간대", "대여예측건수", "반납예측건수"
    ]].copy()
    result = result.merge(
        prediction,
        on=["대여소_ID", "기준_시간대"],
        how="left",
        validate="one_to_one",
    )
    if result[["대여예측건수", "반납예측건수"]].isna().any().any():
        raise ValueError("실제 이용량과 결합할 수 없는 시간대별 예측값이 있습니다.")

    result["대여차이"] = result["대여실적"] - result["대여예측건수"]
    result["반납차이"] = result["반납실적"] - result["반납예측건수"]

    return result[ACTUAL_COLUMNS].sort_values(
        ["기준_날짜", "기준_시간대", "대여소_ID"]
    ).reset_index(drop=True)


def _load_inventory_bounds(
    operations_path: Path,
    target_day: pd.Timestamp,
) -> pd.DataFrame:
    """기존 운영 결과에서 시간대별 적정 재고 하한·상한을 읽는다."""
    data = read_csv_auto(operations_path, dtype={"대여소_ID": "string"})
    missing = [column for column in BOUND_COLUMNS if column not in data.columns]
    if missing:
        raise ValueError(f"운영 결과 파일에 필요한 컬럼이 없습니다: {missing}")

    data = data[BOUND_COLUMNS].copy()
    data["기준_날짜"] = pd.to_datetime(data["기준_날짜"], errors="coerce").dt.normalize()
    data["대여소_ID"] = data["대여소_ID"].astype("string").str.strip()
    data["기준_시간대"] = pd.to_numeric(data["기준_시간대"], errors="coerce")
    data["하한재고"] = pd.to_numeric(data["하한재고"], errors="coerce")
    data["상한재고"] = pd.to_numeric(data["상한재고"], errors="coerce")
    data = data[data["기준_날짜"].eq(target_day)].copy()
    data = data.dropna(subset=["대여소_ID", "기준_시간대"])
    data["기준_시간대"] = data["기준_시간대"].astype(int)

    if data.duplicated(["대여소_ID", "기준_시간대"]).any():
        raise ValueError("운영 결과에 중복된 정류소·시간대 적정 재고 범위가 있습니다.")
    return data


def _classify_stock(stock: float, lower: float, upper: float) -> tuple[str, float, float]:
    """재고를 적정 범위와 비교해 운영 상태와 부족·초과량을 계산한다."""
    if pd.isna(lower) or pd.isna(upper):
        return "범위 산출 불가", 0.0, 0.0
    shortage = max(float(lower) - float(stock), 0.0)
    surplus = max(float(stock) - float(upper), 0.0)
    if shortage > 0:
        status = "공급부족"
    elif surplus > 0:
        status = "공급과잉"
    else:
        status = "적정"
    return status, shortage, surplus


def _build_recent_comparison(
    actual_hourly: pd.DataFrame,
    reference_hour: int,
    recent_window_hours: int,
) -> pd.DataFrame:
    """기준 시각 직전 2~3시간의 실제 실적과 예측값을 정류소별로 비교한다."""
    start_hour = max(0, reference_hour - recent_window_hours)
    recent = actual_hourly[
        actual_hourly["기준_시간대"].ge(start_hour)
        & actual_hourly["기준_시간대"].lt(reference_hour)
    ]

    comparison = recent.groupby("대여소_ID").agg(
        실적비교시간수=("기준_시간대", "nunique"),
        최근_대여실적=("대여실적", "sum"),
        최근_반납실적=("반납실적", "sum"),
        최근_대여예측=("대여예측건수", "sum"),
        최근_반납예측=("반납예측건수", "sum"),
    ).reset_index()

    all_stations = actual_hourly[["대여소_ID"]].drop_duplicates()
    comparison = all_stations.merge(comparison, on="대여소_ID", how="left")
    numeric_cols = [
        "실적비교시간수", "최근_대여실적", "최근_반납실적",
        "최근_대여예측", "최근_반납예측",
    ]
    comparison[numeric_cols] = comparison[numeric_cols].fillna(0)
    comparison["최근_대여차이"] = comparison["최근_대여실적"] - comparison["최근_대여예측"]
    comparison["최근_반납차이"] = comparison["최근_반납실적"] - comparison["최근_반납예측"]
    comparison["최근_대여차이율(%)"] = np.where(
        comparison["최근_대여예측"] > 0,
        comparison["최근_대여차이"] / comparison["최근_대여예측"] * 100,
        np.nan,
    )
    comparison["최근_반납차이율(%)"] = np.where(
        comparison["최근_반납예측"] > 0,
        comparison["최근_반납차이"] / comparison["최근_반납예측"] * 100,
        np.nan,
    )
    comparison["실적비교_시작시간"] = start_hour
    comparison["실적비교_종료시간"] = reference_hour - 1
    return comparison


def _build_future_projection(
    actual_hourly: pd.DataFrame,
    station_result: pd.DataFrame,
    bounds: pd.DataFrame,
    reference_hour: int,
) -> pd.DataFrame:
    """기준 시각의 추정 재고를 출발점으로 남은 시간의 예측 재고를 계산한다."""
    bounds_lookup = bounds.set_index(["대여소_ID", "기준_시간대"]).to_dict("index")
    start_lookup = station_result.set_index("대여소_ID").to_dict("index")
    rows: list[dict[str, Any]] = []

    future_actual = actual_hourly[actual_hourly["기준_시간대"].ge(reference_hour)]
    for station_id, station_hours in future_actual.groupby("대여소_ID", sort=False):
        station_info = start_lookup[station_id]
        raw_current_stock = int(station_info["현재추정재고_원계산"])
        stock = max(raw_current_stock, 0)
        initial_missing = bool(station_info["초기재고_누락여부"])
        negative_adjustment = raw_current_stock < 0

        for item in station_hours.sort_values("기준_시간대").to_dict("records"):
            hour = int(item["기준_시간대"])
            rental_demand = int(np.floor(float(item["대여예측건수"]) + 0.5))
            predicted_returns = int(np.floor(float(item["반납예측건수"]) + 0.5))
            served_rental = min(rental_demand, stock)
            unmet_rental = rental_demand - served_rental
            ending_stock = stock - served_rental + predicted_returns

            bound = bounds_lookup.get((station_id, hour), {})
            lower = bound.get("하한재고", np.nan)
            upper = bound.get("상한재고", np.nan)
            status, shortage, surplus = _classify_stock(ending_stock, lower, upper)

            rows.append({
                "대여소_ID": station_id,
                "기준_날짜": item["기준_날짜"],
                "기준_시간대": hour,
                "시작재고": stock,
                "대여예측건수": item["대여예측건수"],
                "반납예측건수": item["반납예측건수"],
                "예상_충족대여량": served_rental,
                "예상_미충족대여수요": unmet_rental,
                "예상_반납량": predicted_returns,
                "종료재고": ending_stock,
                "하한재고": lower,
                "상한재고": upper,
                "부족량": shortage,
                "초과량": surplus,
                "운영상태": status,
                "초기재고_누락여부": initial_missing,
                "기준시각_음수재고보정여부": negative_adjustment,
            })
            stock = ending_stock

    return pd.DataFrame(rows).sort_values(
        ["기준_시간대", "대여소_ID"]
    ).reset_index(drop=True)


def run_rolling_operations(
    target_date: str | date | datetime | pd.Timestamp,
    reference_hour: int,
    recent_window_hours: int = 3,
    prediction_path: str | Path | None = None,
    history_path: str | Path | None = None,
    operations_path: str | Path | None = None,
    data_dir: str | Path = DATA_DIR,
    station_path: str | Path = STATION_PATH,
) -> dict[str, Any]:
    """기준 시각까지 실제 실적을 반영해 현재·잔여 운영 상태를 계산한다.

    reference_hour=9는 09:00 기준을 뜻한다. 실제 누적 실적은 00~08시를
    포함하고, 잔여 예측은 09~23시를 대상으로 한다. 최근 실적 비교 구간은
    reference_hour 직전의 완료된 2시간 또는 3시간이다.

    초기 재고 기록이 누락된 정류소는 기존 운영 정책에 따라 0대로 대체한다.
    다만 누락 사실을 결과에 표시한다. 계산상 재고가 음수가 되는 경우에는
    표시용 추정 재고를 0으로 보정하고, 보정 여부를 결과에 기록한다.
    """
    target_day = _parse_target_date(target_date)
    if not isinstance(reference_hour, (int, np.integer)) or not 0 <= int(reference_hour) <= 23:
        raise ValueError("reference_hour는 0~23 사이의 정수여야 합니다.")
    if recent_window_hours not in (2, 3):
        raise ValueError("recent_window_hours는 2 또는 3만 사용할 수 있습니다.")

    date_label = target_day.strftime("%Y%m%d")
    prediction_file = _resolve_prediction_path(target_day, prediction_path)
    prediction = load_prediction_data(prediction_file)
    prediction = prediction[prediction["기준_날짜"].eq(target_day)].copy()
    if prediction.empty:
        raise ValueError(f"{date_label} 날짜의 수요 예측 데이터가 없습니다.")

    station_ids = set(prediction["대여소_ID"].astype(str))
    expected_hours = set(range(24))
    hour_check = prediction.groupby("대여소_ID")["기준_시간대"].agg(lambda values: set(values))
    invalid_stations = hour_check[hour_check.map(lambda values: values != expected_hours)].index.tolist()
    if invalid_stations:
        raise ValueError(
            "24시간 예측이 완성되지 않은 정류소가 있습니다: "
            f"{invalid_stations[:10]}"
        )

    history_file = _resolve_history_path(target_day, history_path)
    history = load_inventory_history(history_file)
    initial_time = target_day
    initial_snapshot = history[history["기준시각"].eq(initial_time)].copy()
    initial_inventory = prepare_initial_inventory(prediction, initial_snapshot)
    snapshot_ids = set(initial_snapshot["정류소_ID"].astype(str))
    initial_inventory["초기재고_누락여부"] = ~initial_inventory["대여소_ID"].astype(str).isin(snapshot_ids)

    normal_od = _load_daily_actual_od(target_day, data_dir, station_path)
    actual_hourly = _build_actual_hourly(normal_od, prediction, target_day)

    initial_by_station = initial_inventory.set_index("대여소_ID")
    cumulative = actual_hourly[actual_hourly["기준_시간대"].lt(int(reference_hour))].groupby("대여소_ID").agg(
        누적_대여실적=("대여실적", "sum"),
        누적_반납실적=("반납실적", "sum"),
    )
    station_base = pd.DataFrame({"대여소_ID": sorted(station_ids)}).set_index("대여소_ID")
    station_base = station_base.join(initial_by_station[["초기재고", "초기재고_누락여부"]], how="left")
    station_base = station_base.join(cumulative, how="left").fillna({
        "누적_대여실적": 0, "누적_반납실적": 0,
    })
    station_base["누적_대여실적"] = station_base["누적_대여실적"].astype(int)
    station_base["누적_반납실적"] = station_base["누적_반납실적"].astype(int)
    station_base["현재추정재고_원계산"] = (
        station_base["초기재고"]
        + station_base["누적_반납실적"]
        - station_base["누적_대여실적"]
    ).astype(int)
    station_base["현재추정재고_음수보정여부"] = station_base["현재추정재고_원계산"].lt(0)
    station_base["현재추정재고"] = station_base["현재추정재고_원계산"].clip(lower=0).astype(int)

    recent_comparison = _build_recent_comparison(
        actual_hourly, int(reference_hour), int(recent_window_hours)
    )
    station_result = station_base.reset_index().merge(
        recent_comparison, on="대여소_ID", how="left", validate="one_to_one"
    )

    operations_file = _resolve_operations_path(target_day, operations_path)
    bounds = _load_inventory_bounds(operations_file, target_day)
    current_bounds = bounds[bounds["기준_시간대"].eq(int(reference_hour))][
        ["대여소_ID", "하한재고", "상한재고", "범위상태"]
    ].copy()
    station_result = station_result.merge(
        current_bounds, on="대여소_ID", how="left", validate="one_to_one"
    )

    classifications = station_result.apply(
        lambda row: _classify_stock(row["현재추정재고"], row["하한재고"], row["상한재고"]),
        axis=1,
    )
    station_result[["운영상태", "부족량", "초과량"]] = pd.DataFrame(
        classifications.tolist(), index=station_result.index
    )
    station_result["기준_날짜"] = target_day
    station_result["기준_시각"] = f"{int(reference_hour):02d}:00"
    station_result["초기재고_누락여부"] = station_result["초기재고_누락여부"].fillna(False).astype(bool)

    hourly_result = _build_future_projection(
        actual_hourly, station_result, bounds, int(reference_hour)
    )

    compare_start = max(0, int(reference_hour) - int(recent_window_hours))
    compare_rows = actual_hourly[
        actual_hourly["기준_시간대"].ge(compare_start)
        & actual_hourly["기준_시간대"].lt(int(reference_hour))
    ]
    summary = {
        "기준_날짜": date_label,
        "기준_시각": f"{int(reference_hour):02d}:00",
        "실적비교구간": (
            f"{compare_start:02d}:00~{int(reference_hour) - 1:02d}:59"
            if int(reference_hour) > compare_start else "비교 가능한 완료 시간대 없음"
        ),
        "대상정류소수": int(station_result["대여소_ID"].nunique()),
        "공급부족정류소수": int(station_result["운영상태"].eq("공급부족").sum()),
        "공급과잉정류소수": int(station_result["운영상태"].eq("공급과잉").sum()),
        "적정정류소수": int(station_result["운영상태"].eq("적정").sum()),
        "범위산출불가정류소수": int(station_result["운영상태"].eq("범위 산출 불가").sum()),
        "현재부족량합계": int(station_result["부족량"].sum()),
        "현재초과량합계": int(station_result["초과량"].sum()),
        "최근대여실적": int(compare_rows["대여실적"].sum()),
        "최근대여예측": float(compare_rows["대여예측건수"].sum()),
        "최근대여차이": float(compare_rows["대여차이"].sum()),
        "최근반납실적": int(compare_rows["반납실적"].sum()),
        "최근반납예측": float(compare_rows["반납예측건수"].sum()),
        "최근반납차이": float(compare_rows["반납차이"].sum()),
        "초기재고누락정류소수": int(station_result["초기재고_누락여부"].sum()),
        "음수재고보정정류소수": int(station_result["현재추정재고_음수보정여부"].sum()),
    }

    return {
        "summary": summary,
        "station_result": station_result.sort_values("대여소_ID").reset_index(drop=True),
        "hourly_result": hourly_result,
        "actual_hourly": actual_hourly,
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="기준 시각별 운영 실적 및 잔여 재고 분석")
    parser.add_argument("--target-date", required=True, help="분석 기준일 YYYYMMDD")
    parser.add_argument("--reference-hour", type=int, required=True, help="분석 기준 시각 0~23")
    parser.add_argument("--recent-window-hours", type=int, choices=[2, 3], default=3)
    args = parser.parse_args()

    result = run_rolling_operations(
        target_date=args.target_date,
        reference_hour=args.reference_hour,
        recent_window_hours=args.recent_window_hours,
    )
    print("운영 분석 완료")
    for key, value in result["summary"].items():
        print(f"{key}: {value}")
    print(f"정류소별 현재 상태: {len(result['station_result']):,}행")
    print(f"잔여 시간별 예상 상태: {len(result['hourly_result']):,}행")
