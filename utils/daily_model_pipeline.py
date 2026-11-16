r"""ttareungi_mvp의 일별 대여·반납 수요 모델 갱신 파이프라인.

실행 순서
---------
1. 예측 기준일 전날까지 최근 90일의 OD CSV 파일을 불러온다.
2. 기존 프로젝트의 강서구 필터와 정상·불량 의심 데이터 분리 기준을 적용한다.
3. 정류소·날짜·시간대별 대여량과 반납량을 집계한다.
4. 마지막 14일을 검증 데이터로 사용해 고정 파라미터 모델의 성능을 확인한다.
5. 검증 후 최근 90일 전체 데이터로 모델을 다시 학습하고 날짜별로 저장한다.
6. 대여·반납 모델과 메타데이터가 모두 저장된 뒤 models/latest.json을 갱신한다.

프로젝트 루트에서 실행:
    .venv\Scripts\python.exe -m utils.daily_model_pipeline

특정 날짜를 기준으로 테스트 실행:
    .venv\Scripts\python.exe -m utils.daily_model_pipeline --target-date 20261105
"""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder


# -----------------------------------------------------------------------------
# 1. 프로젝트 경로 및 모델 설정
# -----------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data" / "daily_data"
STATION_PATH = BASE_DIR / "data" / "서울시 따릉이대여소 마스터 정보.csv"
MODEL_DIR = BASE_DIR / "models"
OUTPUT_DIR = BASE_DIR / "outputs"

ENCODINGS = ("utf-8-sig", "cp949")
TRAINING_DAYS = 90
VALIDATION_DAYS = 14

# 이전 모델링 과정에서 선정한 Random Forest 파라미터를 유지한다.
RF_PARAMS: dict[str, Any] = {
    "n_estimators": 300,
    "max_depth": None,
    "min_samples_split": 2,
    "min_samples_leaf": 4,
    "max_features": 1.0,
    "bootstrap": True,
    "random_state": 42,
    "n_jobs": -1,
}

RENTAL_FEATURES = ["시작_대여소_ID", "기준_시간대", "요일"]
RENTAL_TARGET = "대여건수"

RETURN_FEATURES = ["종료_대여소_ID", "기준_시간대", "요일"]
RETURN_TARGET = "반납건수"


# -----------------------------------------------------------------------------
# 2. 공통 데이터 로딩 함수
# -----------------------------------------------------------------------------

def read_csv_auto(
    file_path: str | Path, dtype: dict[str, Any] | None = None
) -> pd.DataFrame:
    """프로젝트에서 정한 인코딩 순서로 CSV 파일을 읽는다."""
    file_path = Path(file_path)
    last_error: UnicodeDecodeError | None = None

    for encoding in ENCODINGS:
        try:
            return pd.read_csv(file_path, encoding=encoding, dtype=dtype)
        except UnicodeDecodeError as exc:
            last_error = exc

    raise UnicodeError(f"CSV 인코딩을 확인할 수 없습니다: {file_path}") from last_error


def load_gangseo_station_ids(station_path: str | Path = STATION_PATH) -> list[str]:
    """강서구에 속하고 좌표가 유효한 대여소 ID를 문자열로 반환한다."""
    station_info = read_csv_auto(station_path, dtype={"대여소_ID": "string"})
    required_cols = ["대여소_ID", "주소1", "위도"]
    missing_cols = [col for col in required_cols if col not in station_info.columns]

    if missing_cols:
        raise ValueError(f"대여소 마스터 필수 컬럼 누락: {missing_cols}")

    latitude = pd.to_numeric(station_info["위도"], errors="coerce")
    station_mask = (
        station_info["주소1"].astype("string").str.contains("강서구", na=False)
        & latitude.notna()
        & latitude.ne(0)
    )

    station_ids = (
        station_info.loc[station_mask, "대여소_ID"]
        .dropna()
        .astype("string")
        .str.strip()
    )
    station_ids = station_ids[station_ids.ne("")].drop_duplicates().tolist()

    if not station_ids:
        raise ValueError("강서구 유효 대여소 ID를 찾지 못했습니다.")

    return station_ids


# -----------------------------------------------------------------------------
# 3. 원천 OD 데이터 로딩 및 기존 필터 적용
# -----------------------------------------------------------------------------

def find_missing_daily_dates(
    start_date: date,
    end_date: date,
    folder_path: str | Path = DATA_DIR,
) -> list[date]:
    """지정 기간에 원천 OD CSV가 없는 날짜를 반환한다."""
    folder = Path(folder_path)
    dates = pd.date_range(start_date, end_date, freq="D")
    return [
        timestamp.date()
        for timestamp in dates
        if not (folder / f"tpss_bcycl_od_statnhm_{timestamp:%Y%m%d}.csv").exists()
    ]


def load_data(
    start_date_str: str,
    end_date_str: str,
    folder_path: str | Path = DATA_DIR,
    station_path: str | Path = STATION_PATH,
    station_ids: list[str] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """OD 파일을 불러와 정상 데이터와 불량 의심 데이터를 반환한다.

    기존 프로젝트의 데이터 기준을 유지한다.
    - 출발·도착 정류소가 모두 유효한 강서구 정류소여야 한다.
    - 정상 이용: 이용 시간 1분 이상, 이용 거리 100m 이상.
    - 불량 의심: 출발·도착 정류소가 같고, 이용 시간이 결측이며,
      이용 거리가 결측이거나 100m 미만인 데이터.
    - HHMM 형식의 시간 값을 100으로 나누어 0~23시로 변환한다.
    """
    start_date = datetime.strptime(start_date_str, "%Y%m%d").date()
    end_date = datetime.strptime(end_date_str, "%Y%m%d").date()

    if end_date < start_date:
        raise ValueError("종료일은 시작일보다 빠를 수 없습니다.")

    folder_path = Path(folder_path)
    if station_ids is None:
        station_ids = load_gangseo_station_ids(station_path)
    station_id_set = set(station_ids)

    all_normal: list[pd.DataFrame] = []
    all_suspect: list[pd.DataFrame] = []
    missing_files: list[str] = []

    current_date = start_date
    while current_date <= end_date:
        date_str = current_date.strftime("%Y%m%d")
        file_name = f"tpss_bcycl_od_statnhm_{date_str}.csv"
        file_path = folder_path / file_name

        if not file_path.exists():
            missing_files.append(file_name)
            current_date += timedelta(days=1)
            continue

        df = read_csv_auto(
            file_path,
            dtype={"시작_대여소": "string", "종료_대여소": "string"},
        )
        df = df.rename(columns={
            "기준_시간": "기준_시간대",
            "시작_대여소": "시작_대여소_ID",
            "종료_대여소": "종료_대여소_ID",
            "전체건수": "전체_건수",
        })

        required_cols = [
            "기준_시간대", "시작_대여소_ID", "종료_대여소_ID",
            "집계_기준", "전체_이용_분", "전체_이용_거리",
        ]
        missing_cols = [col for col in required_cols if col not in df.columns]
        if missing_cols:
            raise ValueError(f"{file_name} 필수 컬럼 누락: {missing_cols}")

        df["기준_날짜"] = pd.Timestamp(current_date)
        df["시작_대여소_ID"] = df["시작_대여소_ID"].astype("string").str.strip()
        df["종료_대여소_ID"] = df["종료_대여소_ID"].astype("string").str.strip()

        gangseo_mask = (
            df["시작_대여소_ID"].isin(station_id_set)
            & df["종료_대여소_ID"].isin(station_id_set)
        )
        gangseo_df = df.loc[gangseo_mask].copy()

        duration = pd.to_numeric(gangseo_df["전체_이용_분"], errors="coerce")
        distance = pd.to_numeric(gangseo_df["전체_이용_거리"], errors="coerce")

        valid_mask = (duration >= 1) & (distance >= 100)
        defective_mask = (
            gangseo_df["시작_대여소_ID"].eq(gangseo_df["종료_대여소_ID"])
            & duration.isna()
            & (distance.isna() | distance.lt(100))
        )

        normal_df = gangseo_df.loc[valid_mask].copy()
        suspect_df = gangseo_df.loc[defective_mask].copy()

        normal_df = normal_df[normal_df["종료_대여소_ID"].ne("X")].copy()
        suspect_df = suspect_df[suspect_df["종료_대여소_ID"].ne("X")].copy()

        for part in (normal_df, suspect_df):
            time_value = pd.to_numeric(part["기준_시간대"], errors="coerce")
            part["기준_시간대"] = (time_value // 100)
            part.dropna(subset=["기준_시간대"], inplace=True)
            part["기준_시간대"] = part["기준_시간대"].astype(int)
            part.drop(
                index=part.index[~part["기준_시간대"].between(0, 23)],
                inplace=True,
            )

        all_normal.append(normal_df)
        all_suspect.append(suspect_df)
        current_date += timedelta(days=1)

    # 누락 날짜는 0건으로 대체하지 않는다. 해당 날짜는 학습에서 제외한다.
    if missing_files:
        preview = ", ".join(missing_files[:10])
        suffix = " ..." if len(missing_files) > 10 else ""
        print(
            f"주의: 일별 원천 파일 {len(missing_files)}개가 없습니다. "
            f"누락 날짜는 0으로 채우지 않고 학습에서 제외합니다: {preview}{suffix}"
        )

    normal_df = pd.concat(all_normal, ignore_index=True) if all_normal else pd.DataFrame()
    suspect_df = pd.concat(all_suspect, ignore_index=True) if all_suspect else pd.DataFrame()

    if normal_df.empty:
        raise ValueError("최근 90일 정상 이용 데이터가 없습니다.")

    return normal_df, suspect_df


# -----------------------------------------------------------------------------
# 4. 날짜·정류소·시간대별 대여·반납 집계
# -----------------------------------------------------------------------------

def aggregate_hourly_counts(
    data: pd.DataFrame,
    station_col: str,
    target_col: str,
    start_date: date,
    end_date: date,
) -> pd.DataFrame:
    """날짜 × 관측된 정류소 × 24시간 조합을 만들고 이용이 없는 구간은 0으로 채운다.

    기존 노트북의 집계 방식을 유지한다. 대여 정류소 목록과 반납 정류소
    목록은 각각의 출발·도착 데이터에서 독립적으로 가져온다.
    """
    grouped = data.groupby(
        ["기준_날짜", station_col, "기준_시간대"]
    ).size().rename(target_col)

    if grouped.empty:
        raise ValueError(f"{target_col} 집계 결과가 없습니다.")

    # 실제 OD 행이 존재하는 날짜만 사용한다. 누락된 원천 파일의 날짜를
    # 날짜 × 정류소 × 시간 인덱스에 포함하면 가짜 0건 학습 데이터가 생긴다.
    observed_dates = pd.to_datetime(data["기준_날짜"], errors="coerce").dropna().dt.normalize()
    dates = pd.DatetimeIndex(observed_dates.unique()).sort_values()
    dates = dates[(dates >= pd.Timestamp(start_date)) & (dates <= pd.Timestamp(end_date))]
    if dates.empty:
        raise ValueError(f"{target_col}: 실제 관측된 날짜가 없습니다.")

    observed_station_ids = grouped.index.get_level_values(station_col).unique()
    full_index = pd.MultiIndex.from_product(
        [dates, observed_station_ids, range(24)],
        names=["기준_날짜", station_col, "기준_시간대"],
    )

    result = grouped.reindex(full_index, fill_value=0).reset_index()
    result["요일"] = result["기준_날짜"].dt.dayofweek
    result[target_col] = result[target_col].astype("int32")

    return result


def build_model_datasets(
    normal_df: pd.DataFrame,
    start_date: date,
    end_date: date,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """대여 모델과 반납 모델의 학습용 데이터를 각각 생성한다."""
    start_df = normal_df[normal_df["집계_기준"].eq("출발시간")].copy()
    end_df = normal_df[normal_df["집계_기준"].eq("도착시간")].copy()

    if start_df.empty:
        raise ValueError("출발시간 기준 대여 데이터가 없습니다.")
    if end_df.empty:
        raise ValueError("도착시간 기준 반납 데이터가 없습니다.")

    rental_data = aggregate_hourly_counts(
        start_df, "시작_대여소_ID", "대여건수",
        start_date, end_date,
    )
    return_data = aggregate_hourly_counts(
        end_df, "종료_대여소_ID", "반납건수",
        start_date, end_date,
    )

    return rental_data, return_data


# -----------------------------------------------------------------------------
# 5. scikit-learn 모델 파이프라인 및 시간 순서 기반 검증
# -----------------------------------------------------------------------------

def make_random_forest_pipeline(
    feature_cols: list[str],
    model_params: dict[str, Any] | None = None,
) -> Pipeline:
    """범주형 변수 전처리와 Random Forest를 하나의 저장 가능한 파이프라인으로 구성한다."""
    params = RF_PARAMS.copy()
    if model_params:
        params.update(model_params)

    categorical_encoder = ColumnTransformer(
        transformers=[
            (
                "categorical",
                OneHotEncoder(handle_unknown="ignore"),
                feature_cols,
            )
        ],
        remainder="drop",
    )

    return Pipeline(steps=[
        ("preprocessor", categorical_encoder),
        ("model", RandomForestRegressor(**params)),
    ])


def prepare_features(data: pd.DataFrame, feature_cols: list[str]) -> pd.DataFrame:
    """모델 입력값을 정규화한다. 현재 사용하는 세 입력 변수는 모두 범주형으로 처리한다."""
    missing_cols = [col for col in feature_cols if col not in data.columns]
    if missing_cols:
        raise ValueError(f"모델 입력 컬럼 누락: {missing_cols}")

    features = data[feature_cols].copy()
    for col in feature_cols:
        features[col] = features[col].astype("string").fillna("__MISSING__")

    return features


def split_train_validation(
    data: pd.DataFrame,
    feature_cols: list[str],
    target_col: str,
    validation_start: pd.Timestamp,
) -> tuple[pd.DataFrame, pd.Series, pd.DataFrame, pd.Series]:
    """날짜 순서를 유지해 학습·검증 데이터를 나눈다. 검증 기간은 섞지 않는다."""
    work = data.copy()
    work["기준_날짜"] = pd.to_datetime(work["기준_날짜"])
    work[target_col] = pd.to_numeric(work[target_col], errors="coerce")
    work = work.dropna(subset=feature_cols + [target_col])

    train_data = work[work["기준_날짜"] < validation_start]
    valid_data = work[work["기준_날짜"] >= validation_start]

    if train_data.empty or valid_data.empty:
        raise ValueError(f"{target_col}: 학습 또는 검증 데이터가 비어 있습니다.")

    X_train = prepare_features(train_data, feature_cols)
    y_train = train_data[target_col].astype(float)
    X_valid = prepare_features(valid_data, feature_cols)
    y_valid = valid_data[target_col].astype(float)

    return X_train, y_train, X_valid, y_valid


def fit_evaluate_and_refit(
    data: pd.DataFrame,
    feature_cols: list[str],
    target_col: str,
    validation_start: pd.Timestamp,
    model_path: Path,
) -> dict[str, float | int | None]:
    """마지막 기간으로 성능을 검증한 뒤 최근 90일 전체 데이터로 다시 학습해 저장한다."""
    X_train, y_train, X_valid, y_valid = split_train_validation(
        data, feature_cols, target_col, validation_start
    )

    # 검증용 모델: 최근 90일 중 검증 시작일 이전의 데이터만 사용한다.
    validation_model = make_random_forest_pipeline(feature_cols)
    validation_model.fit(X_train, y_train)
    valid_predictions = validation_model.predict(X_valid)

    r2_value = r2_score(y_valid, valid_predictions) if len(y_valid) > 1 else None
    metrics: dict[str, float | int | None] = {
        "train_rows": int(len(y_train)),
        "validation_rows": int(len(y_valid)),
        "MAE": float(mean_absolute_error(y_valid, valid_predictions)),
        "RMSE": float(np.sqrt(mean_squared_error(y_valid, valid_predictions))),
        "R2": float(r2_value) if r2_value is not None else None,
    }

    # 최종 모델: 새로운 파이프라인을 만들고 최근 90일 전체 데이터로 학습한다.
    final_model = make_random_forest_pipeline(feature_cols)
    X_all = prepare_features(data, feature_cols)
    y_all = pd.to_numeric(data[target_col], errors="coerce")
    valid_target_mask = y_all.notna()
    X_all = X_all.loc[valid_target_mask]
    y_all = y_all.loc[valid_target_mask].astype(float)

    if X_all.empty:
        raise ValueError(f"{target_col}: 최종 학습 데이터가 비어 있습니다.")

    final_model.fit(X_all, y_all)
    model_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(final_model, model_path)

    metrics["final_training_rows"] = int(len(y_all))
    return metrics


# -----------------------------------------------------------------------------
# 6. 일일 갱신 실행 및 날짜별 모델 버전 관리
# -----------------------------------------------------------------------------

def _parse_target_date(target_date: str | date | datetime | None) -> date:
    if target_date is None:
        return date.today()
    if isinstance(target_date, datetime):
        return target_date.date()
    if isinstance(target_date, date):
        return target_date
    return datetime.strptime(target_date, "%Y%m%d").date()


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with open(temp_path, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=4)
    temp_path.replace(path)


def run_daily_model_update(
    target_date: str | date | datetime | None = None,
    validation_days: int = VALIDATION_DAYS,
) -> dict[str, Any]:
    """예측 기준일 직전 90일의 데이터로 대여·반납 모델을 학습하고 날짜별로 저장한다."""
    target_day = _parse_target_date(target_date)
    end_date = target_day - timedelta(days=1)
    start_date = end_date - timedelta(days=TRAINING_DAYS - 1)
    model_date = target_day.strftime("%Y%m%d")

    if not 1 <= validation_days < TRAINING_DAYS:
        raise ValueError("validation_days는 1~89 사이여야 합니다.")

    validation_start = pd.Timestamp(end_date) - pd.Timedelta(days=validation_days - 1)
    expected_days = (end_date - start_date).days + 1
    missing_dates = find_missing_daily_dates(start_date, end_date, DATA_DIR)
    available_days = expected_days - len(missing_dates)

    print(f"모델 기준일: {model_date}")
    print(f"학습 데이터 기간: {start_date:%Y%m%d} ~ {end_date:%Y%m%d}")
    print(f"예상 원천 파일: {expected_days}일 / 실제 파일: {available_days}일 / 누락: {len(missing_dates)}일")
    print(f"검증 기간: {validation_start:%Y%m%d} ~ {end_date:%Y%m%d} ({validation_days}일)")

    missing_validation_dates = [day for day in missing_dates if day >= validation_start.date()]
    if missing_validation_dates:
        missing_text = ", ".join(day.strftime("%Y%m%d") for day in missing_validation_dates)
        raise FileNotFoundError(
            "검증 기간에 원천 파일 누락이 있습니다. 검증 성능을 온전한 14일 기준으로 비교할 수 없어 중단합니다: "
            f"{missing_text}"
        )

    if available_days == 0:
        raise FileNotFoundError("학습 기간에 사용할 수 있는 일별 OD 파일이 없습니다.")

    station_ids = load_gangseo_station_ids(STATION_PATH)
    normal_df, suspect_df = load_data(
        start_date.strftime("%Y%m%d"),
        end_date.strftime("%Y%m%d"),
        DATA_DIR,
        STATION_PATH,
        station_ids=station_ids,
    )

    rental_data, return_data = build_model_datasets(
        normal_df, start_date, end_date
    )

    expected_validation_dates = set(pd.date_range(validation_start, end_date, freq="D"))
    for label, model_data in (("대여", rental_data), ("반납", return_data)):
        observed_validation_dates = set(
            pd.to_datetime(model_data.loc[model_data["기준_날짜"] >= validation_start, "기준_날짜"])
            .dt.normalize()
            .tolist()
        )
        missing_observed_dates = sorted(expected_validation_dates - observed_validation_dates)
        if missing_observed_dates:
            missing_text = ", ".join(day.strftime("%Y%m%d") for day in missing_observed_dates)
            raise ValueError(
                f"{label} 검증 데이터에 정상 이용 기록이 없는 날짜가 있습니다: {missing_text}. "
                "14일 검증을 온전히 수행할 수 없어 모델 학습을 중단합니다."
            )

    # 임시 폴더에서 먼저 모델을 생성한다.
    # 두 모델 중 하나라도 학습·저장에 실패하면 기존 모델 버전을 유지한다.
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    staging_dir = MODEL_DIR / f".{model_date}.staging"
    final_dir = MODEL_DIR / model_date
    backup_dir = MODEL_DIR / f".{model_date}.backup"

    for path in (staging_dir, backup_dir):
        if path.exists():
            shutil.rmtree(path)
    staging_dir.mkdir(parents=True)

    try:
        rental_metrics = fit_evaluate_and_refit(
            rental_data,
            RENTAL_FEATURES,
            RENTAL_TARGET,
            validation_start,
            staging_dir / "rental_rf.joblib",
        )

        return_metrics = fit_evaluate_and_refit(
            return_data,
            RETURN_FEATURES,
            RETURN_TARGET,
            validation_start,
            staging_dir / "return_rf.joblib",
        )

        metadata: dict[str, Any] = {
            "model_date": model_date,
            "training_start": start_date.strftime("%Y%m%d"),
            "training_end": end_date.strftime("%Y%m%d"),
            "validation_start": validation_start.strftime("%Y%m%d"),
            "validation_end": end_date.strftime("%Y%m%d"),
            "validation_days": validation_days,
            "expected_source_file_days": expected_days,
            "available_source_file_days": available_days,
            "missing_source_dates": [day.strftime("%Y%m%d") for day in missing_dates],
            "rental_observed_days": int(pd.to_datetime(rental_data["기준_날짜"]).dt.normalize().nunique()),
            "return_observed_days": int(pd.to_datetime(return_data["기준_날짜"]).dt.normalize().nunique()),
            "rental_training_observed_days": int(pd.to_datetime(rental_data.loc[rental_data["기준_날짜"] < validation_start, "기준_날짜"]).dt.normalize().nunique()),
            "rental_validation_observed_days": int(pd.to_datetime(rental_data.loc[rental_data["기준_날짜"] >= validation_start, "기준_날짜"]).dt.normalize().nunique()),
            "return_training_observed_days": int(pd.to_datetime(return_data.loc[return_data["기준_날짜"] < validation_start, "기준_날짜"]).dt.normalize().nunique()),
            "return_validation_observed_days": int(pd.to_datetime(return_data.loc[return_data["기준_날짜"] >= validation_start, "기준_날짜"]).dt.normalize().nunique()),
            "rental_station_count": int(rental_data["시작_대여소_ID"].nunique()),
            "return_station_count": int(return_data["종료_대여소_ID"].nunique()),
            "normal_record_count": len(normal_df),
            "suspect_record_count": len(suspect_df),
            "rental_metrics": rental_metrics,
            "return_metrics": return_metrics,
            "model_params": RF_PARAMS,
            "python_version": __import__("platform").python_version(),
            "scikit_learn_version": sklearn.__version__,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        _write_json(staging_dir / "metadata.json", metadata)

        # 불량 의심 데이터는 별도 운영 입력자료로 저장한다.
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        suspect_path = OUTPUT_DIR / f"suspect_usage_candidates_{model_date}.csv"
        suspect_temp_path = suspect_path.with_suffix(".csv.tmp")
        suspect_df.to_csv(suspect_temp_path, index=False, encoding="utf-8-sig")
        
        # 대여·반납 모델이 모두 준비된 뒤 날짜별 모델 폴더를 교체한다.
        if final_dir.exists():
            final_dir.replace(backup_dir)
        try:
            staging_dir.replace(final_dir)
        except Exception:
            if backup_dir.exists() and not final_dir.exists():
                backup_dir.replace(final_dir)
            raise
        if backup_dir.exists():
            shutil.rmtree(backup_dir)
        suspect_temp_path.replace(suspect_path)

        latest_info = {
            "model_date": model_date,
            "rental_model": (final_dir / "rental_rf.joblib").relative_to(BASE_DIR).as_posix(),
            "return_model": (final_dir / "return_rf.joblib").relative_to(BASE_DIR).as_posix(),
            "metadata": (final_dir / "metadata.json").relative_to(BASE_DIR).as_posix(),
        }
        _write_json(MODEL_DIR / "latest.json", latest_info)

    except Exception:
        if staging_dir.exists():
            shutil.rmtree(staging_dir)
        raise

    print(f"모델 갱신 완료: {model_date}")
    print(f"최종 사용 원천 파일: {available_days}/{expected_days}일")
    print(f"대여 데이터 관측일: 전체 {metadata['rental_observed_days']}일 / 학습 {metadata['rental_training_observed_days']}일 / 검증 {metadata['rental_validation_observed_days']}일")
    print(f"반납 데이터 관측일: 전체 {metadata['return_observed_days']}일 / 학습 {metadata['return_training_observed_days']}일 / 검증 {metadata['return_validation_observed_days']}일")
    print("대여 모델 검증 성능:", rental_metrics)
    print("반납 모델 검증 성능:", return_metrics)

    return metadata


def load_latest_models() -> tuple[Pipeline, Pipeline, dict[str, Any]]:
    """정상적으로 저장된 최신 대여·반납 모델을 불러온다."""
    latest_path = MODEL_DIR / "latest.json"
    if not latest_path.exists():
        raise FileNotFoundError("최신 모델 정보가 없습니다. 모델 학습을 먼저 실행하세요.")

    with open(latest_path, "r", encoding="utf-8") as file:
        latest_info = json.load(file)

    rental_path = BASE_DIR / latest_info["rental_model"]
    return_path = BASE_DIR / latest_info["return_model"]
    metadata_path = BASE_DIR / latest_info["metadata"]

    for path in (rental_path, return_path, metadata_path):
        if not path.exists():
            raise FileNotFoundError(f"최신 모델 구성 파일이 없습니다: {path}")

    rental_model = joblib.load(rental_path)
    return_model = joblib.load(return_path)

    with open(metadata_path, "r", encoding="utf-8") as file:
        metadata = json.load(file)

    return rental_model, return_model, metadata


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="일별 대여·반납 수요 모델 갱신")
    parser.add_argument(
        "--target-date",
        default=None,
        help="예측 기준일(YYYYMMDD). 입력하지 않으면 PC의 오늘 날짜를 사용합니다.",
    )
    parser.add_argument(
        "--validation-days",
        type=int,
        default=VALIDATION_DAYS,
        help="검증에 사용할 최근 일수 (기본값: 14일)",
    )
    args = parser.parse_args()
    run_daily_model_update(args.target_date, args.validation_days)
