"""저장된 대여·반납 모델로 정류소별 시간대 수요를 예측합니다.

프로젝트 루트에서 실행:
    .venv\\Scripts\\python.exe -m utils.demand_predictor

특정 날짜 예측:
    .venv\\Scripts\\python.exe -m utils.demand_predictor --start-date 20261030 --end-date 20261030

기간 예측:
    .venv\\Scripts\\python.exe -m utils.demand_predictor --start-date 20261030 --end-date 20261101
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
from sklearn.pipeline import Pipeline

from utils.daily_model_pipeline import (RENTAL_FEATURES,RETURN_FEATURES,load_latest_models,prepare_features)


BASE_DIR = Path(__file__).resolve().parents[1]
OUTPUT_DIR = BASE_DIR / "outputs/prediction_results"


def load_prediction_models() -> tuple[Pipeline, Pipeline, dict]:
    """최신 대여·반납 모델과 메타데이터를 불러온다."""
    return load_latest_models()


def _extract_station_categories(model: Pipeline, station_feature: str) -> set[str]:
    """학습된 OneHotEncoder에서 실제 학습 정류소 ID를 추출한다."""
    try:
        preprocessor = model.named_steps["preprocessor"]
        encoder = preprocessor.named_transformers_["categorical"]
        feature_cols = list(preprocessor.transformers_[0][2])
    except (AttributeError, KeyError, IndexError, TypeError) as exc:
        raise ValueError("저장된 모델 구조가 예상한 Pipeline + ColumnTransformer 형식과 다릅니다.") from exc

    if station_feature not in feature_cols:
        raise ValueError(f"모델 입력 변수에 '{station_feature}'가 없습니다.")

    feature_index = feature_cols.index(station_feature)
    categories = encoder.categories_[feature_index]
    station_ids = {str(value).strip() for value in categories}
    station_ids.discard("")

    if not station_ids:
        raise ValueError(f"'{station_feature}'에서 학습 정류소 ID를 찾지 못했습니다.")

    return station_ids


def get_model_station_ids(rental_model: Pipeline,return_model: Pipeline,) -> list[str]:
    """대여·반납 모델 모두에 학습된 정류소 ID 목록을 반환한다."""
    rental_ids = _extract_station_categories(rental_model, "시작_대여소_ID")
    return_ids = _extract_station_categories(return_model, "종료_대여소_ID")

    rental_only = sorted(rental_ids - return_ids)
    return_only = sorted(return_ids - rental_ids)

    if rental_only or return_only:
        raise ValueError("대여 모델과 반납 모델의 학습 정류소 목록이 일치하지 않습니다. "
                         f"대여 모델에만 존재: {rental_only[:10]}, "
                         f"반납 모델에만 존재: {return_only[:10]}")

    return sorted(rental_ids)

def build_prediction_grid(station_ids: Iterable[str],
                          start_date: str | pd.Timestamp,
                          end_date: str | pd.Timestamp,
                          hours: Iterable[int] = range(24),) -> pd.DataFrame:
    """정류소 × 날짜 × 시간대 조합으로 예측 입력 데이터를 만든다. 종료일 포함."""
    station_series = pd.Series(list(station_ids), dtype="string").dropna().str.strip()
    station_ids = station_series[station_series.ne("")].drop_duplicates().tolist()

    if not station_ids:
        raise ValueError("예측 대상 정류소가 없습니다.")

    start = pd.to_datetime(start_date, errors="raise").normalize()
    end = pd.to_datetime(end_date, errors="raise").normalize()

    if start > end:
        raise ValueError("시작일은 종료일보다 늦을 수 없습니다.")

    hour_values = pd.to_numeric(pd.Series(list(hours)), errors="raise")
    if hour_values.empty:
        raise ValueError("예측할 시간대가 없습니다.")
    if not hour_values.between(0, 23).all() or not (hour_values % 1 == 0).all():
        raise ValueError("시간대는 정수 0~23 사이여야 합니다.")

    hour_values = hour_values.astype(int).tolist()
    if len(hour_values) != len(set(hour_values)):
        raise ValueError("예측 시간대에 중복값이 있습니다.")

    dates = pd.date_range(start=start, end=end, freq="D")
    grid = pd.MultiIndex.from_product(
        [dates, station_ids, hour_values],
        names=["기준_날짜", "대여소_ID", "기준_시간대"],
    ).to_frame(index=False)

    grid["기준_시간대"] = grid["기준_시간대"].astype(int)
    grid["요일"] = grid["기준_날짜"].dt.dayofweek.astype(int)

    return grid

def predict_station_demand(
    prediction_grid: pd.DataFrame,
    rental_model: Pipeline,
    return_model: Pipeline,
) -> pd.DataFrame:
    """정류소별 대여·반납량을 예측하고 재고 변화 관련 지표를 계산한다."""
    required_cols = ["기준_날짜", "대여소_ID", "기준_시간대"]
    missing_cols = [col for col in required_cols if col not in prediction_grid.columns]

    if missing_cols:
        raise ValueError(f"예측 입력에 필수 컬럼이 없습니다: {missing_cols}")
    if prediction_grid.empty:
        raise ValueError("예측 입력 데이터가 비어 있습니다.")

    grid = prediction_grid[required_cols].copy()
    grid["기준_날짜"] = pd.to_datetime(grid["기준_날짜"], errors="raise").dt.normalize()

    if grid["대여소_ID"].isna().any():
        raise ValueError("예측 입력에 정류소 ID 결측값이 있습니다.")
    grid["대여소_ID"] = grid["대여소_ID"].astype("string").str.strip()
    if grid["대여소_ID"].eq("").any():
        raise ValueError("예측 입력에 빈 정류소 ID가 있습니다.")

    hours = pd.to_numeric(grid["기준_시간대"], errors="raise")
    if not hours.between(0, 23).all() or not (hours % 1 == 0).all():
        raise ValueError("기준_시간대는 정수 0~23 사이여야 합니다.")
    grid["기준_시간대"] = hours.astype(int)
    grid["요일"] = grid["기준_날짜"].dt.dayofweek.astype(int)

    key_cols = ["기준_날짜", "대여소_ID", "기준_시간대"]
    if grid.duplicated(key_cols).any():
        raise ValueError("예측 입력에 중복된 날짜·정류소·시간 조합이 있습니다.")

    supported_ids = get_model_station_ids(rental_model, return_model)
    unsupported_ids = sorted(set(grid["대여소_ID"].astype(str)) - set(supported_ids))
    if unsupported_ids:
        raise ValueError(
            "현재 모델이 학습하지 않은 정류소가 예측 입력에 포함되어 있습니다: "
            f"{unsupported_ids[:10]}"
        )

    rental_input = grid[["대여소_ID", "기준_시간대", "요일"]].rename(columns={"대여소_ID": "시작_대여소_ID"})
    return_input = grid[["대여소_ID", "기준_시간대", "요일"]].rename(columns={"대여소_ID": "종료_대여소_ID"})

    rental_features = prepare_features(rental_input, RENTAL_FEATURES)
    return_features = prepare_features(return_input, RETURN_FEATURES)

    rental_predictions = rental_model.predict(rental_features)
    return_predictions = return_model.predict(return_features)

    grid["대여예측건수"] = np.clip(np.asarray(rental_predictions, dtype=float), 0, None)
    grid["반납예측건수"] = np.clip(np.asarray(return_predictions, dtype=float), 0, None)

    grid["예상_재고변화"] = grid["반납예측건수"] - grid["대여예측건수"]
    grid["예상_순유출량"] = grid["대여예측건수"] - grid["반납예측건수"]

    return grid.sort_values(key_cols).reset_index(drop=True)

def run_demand_prediction(
    start_date: str | None = None,
    end_date: str | None = None,
    output_path: str | Path | None = None,
    hours: Iterable[int] = range(24),
) -> pd.DataFrame:
    """최신 모델로 예측을 수행하고 CSV로 저장한 뒤 결과를 반환한다.

    날짜를 생략하면 모델 메타데이터의 model_date를 하루 예측 기준일로 사용한다.
    """
    rental_model, return_model, metadata = load_prediction_models()
    station_ids = get_model_station_ids(rental_model, return_model)

    model_date = metadata.get("model_date")
    if start_date is None:
        if not model_date:
            raise ValueError("모델 메타데이터에 model_date가 없습니다.")
        start_date = model_date
    if end_date is None:
        end_date = start_date

    prediction_grid = build_prediction_grid(
        station_ids=station_ids,
        start_date=start_date,
        end_date=end_date,
        hours=hours,
    )
    prediction_result = predict_station_demand(
        prediction_grid,
        rental_model,
        return_model,
    )

    if output_path is None:
        start_label = pd.to_datetime(start_date).strftime("%Y%m%d")
        end_label = pd.to_datetime(end_date).strftime("%Y%m%d")
        suffix = start_label if start_label == end_label else f"{start_label}_{end_label}"
        output_path = OUTPUT_DIR / f"demand_forecast_{suffix}.csv"
    else:
        output_path = Path(output_path)
        if not output_path.is_absolute():
            output_path = BASE_DIR / output_path

    output_path.parent.mkdir(parents=True, exist_ok=True)
    prediction_result.to_csv(output_path, index=False, encoding="utf-8-sig")

    print(f"사용 모델 기준일: {model_date}")
    print(f"예측 정류소 수: {len(station_ids):,}개")
    print(f"예측 기간: {pd.to_datetime(start_date).date()} ~ {pd.to_datetime(end_date).date()}")
    print(f"예측 결과 행 수: {len(prediction_result):,}건")
    print(f"저장 위치: {output_path}")

    return prediction_result

def main() -> None:
    parser = argparse.ArgumentParser(description="대여·반납 수요 예측 실행")
    parser.add_argument("--start-date", default=None, help="예측 시작일(YYYYMMDD)")
    parser.add_argument("--end-date", default=None, help="예측 종료일(YYYYMMDD, 포함)")
    parser.add_argument("--output", default=None, help="결과 CSV 저장 경로")
    args = parser.parse_args()

    run_demand_prediction(
        start_date=args.start_date,
        end_date=args.end_date,
        output_path=args.output,
    )
if __name__ == "__main__":
    main()