"""수요 예측 결과와 실제 재고 이력을 결합해 운영 현황 데이터를 생성한다.

실행: .venv\\Scripts\\python.exe -m utils.operations_simulator
모든 예측 대상 정류소의 기준일 직전 7일 실제 재고 이력과 기준일 00시 초기 재고를 수집·저장한다.
재고 이력 파일이 없으면 서울시 API를 조회하므로 SEOUL_OPENAPI_KEY를 secrets.toml 또는 환경변수에 설정한다.
"""

from __future__ import annotations

import argparse
import os
import re
import tomllib
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


BASE_DIR = Path(__file__).resolve().parents[1]
OUTPUT_DIR = BASE_DIR / "outputs"
PREDICTION_DIR = OUTPUT_DIR / "prediction_results"
HISTORY_DIR = OUTPUT_DIR / "inventory_history"
OPERATIONS_DIR = OUTPUT_DIR / "operation_results"

HISTORY_DAYS = 7
MIN_STOCK = 5
MIN_RANGE_WIDTH = 10

RESULT_COLUMNS = [
    "기준_날짜", "대여소_ID", "기준_시간대", "대여예측건수", "반납예측건수",
    "대여수요_정수", "반납량_정수", "예상_충족대여량", "시작재고", "종료재고", "예상_미충족대여수요",
    "IFI", "IFI_등급", "목표만족률(%)", "관측일수", "분포기반하한", "분포기반상한",
    "하한재고", "상한재고", "범위상태", "부족량", "초과량", "운영상태",
    "부족우선순위", "초과우선순위",
]


def read_csv_auto(file_path: str | Path, **kwargs: Any) -> pd.DataFrame:
    """UTF-8 또는 CP949 인코딩으로 CSV를 읽는다."""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"파일을 찾을 수 없습니다: {path}")

    for encoding in ("utf-8-sig", "cp949", "utf-8"):
        try:
            return pd.read_csv(path, encoding=encoding, **kwargs)
        except UnicodeDecodeError:
            continue
    raise UnicodeError(f"CSV 인코딩을 확인할 수 없습니다: {path}")


def normalize_snapshot(snapshot: pd.DataFrame) -> pd.DataFrame:
    """API 또는 CSV의 재고 컬럼명을 통일한다."""
    data = snapshot.copy()
    rename = {}
    if "정류소_ID" not in data.columns and "stationId" in data.columns:
        rename["stationId"] = "정류소_ID"
    if "현재재고" not in data.columns and "parkingBikeTotCnt" in data.columns:
        rename["parkingBikeTotCnt"] = "현재재고"
    if "기준시각" not in data.columns and "stationDt" in data.columns:
        rename["stationDt"] = "기준시각"
    data = data.rename(columns=rename)

    required = ["정류소_ID", "현재재고"]
    missing = [col for col in required if col not in data.columns]
    if missing:
        raise ValueError(f"재고 데이터에 필요한 컬럼이 없습니다: {missing}")

    data = data[required + (["기준시각"] if "기준시각" in data.columns else [])].copy()
    data["정류소_ID"] = data["정류소_ID"].astype("string").str.strip()
    data["현재재고"] = pd.to_numeric(data["현재재고"], errors="coerce")
    data = data.dropna(subset=required).copy()
    data = data[data["정류소_ID"].ne("")]
    if (data["현재재고"] < 0).any():
        raise ValueError("재고 데이터에 음수 값이 있습니다.")
    data["현재재고"] = np.floor(data["현재재고"] + 0.5).astype(int)

    if "기준시각" in data.columns:
        raw = data["기준시각"].astype("string").str.strip()
        parsed = pd.to_datetime(raw, format="%Y%m%d%H", errors="coerce")
        needs_fallback = parsed.isna()
        if needs_fallback.any():
            parsed.loc[needs_fallback] = pd.to_datetime(raw.loc[needs_fallback], errors="coerce")
        data["기준시각"] = parsed
        data = data.dropna(subset=["기준시각"])
        data = data.drop_duplicates(["정류소_ID", "기준시각"], keep="last")

    return data.reset_index(drop=True)


def load_inventory_history(history_path: str | Path) -> pd.DataFrame:
    """과거 정류소별 재고 이력을 불러온다."""
    history = read_csv_auto(history_path, dtype={"정류소_ID": "string"})
    data = normalize_snapshot(history)
    if "기준시각" not in data.columns:
        raise ValueError("재고 이력에 '기준시각' 컬럼이 없습니다.")
    return data[["정류소_ID", "현재재고", "기준시각"]]


def parse_snapshot_time(value: str | pd.Timestamp) -> pd.Timestamp:
    """YYYYMMDDHH 또는 일반 날짜 문자열을 시간 단위 Timestamp로 변환한다."""
    text = str(value).strip()
    if re.fullmatch(r"\d{10}", text):
        return pd.to_datetime(text, format="%Y%m%d%H")
    return pd.to_datetime(value, errors="raise").floor("h")


def load_prediction_data(prediction_path: str | Path | None = None) -> pd.DataFrame:
    """최신 수요 예측 결과를 읽고 시뮬레이터 입력 컬럼만 남긴다."""
    if prediction_path is None:
        candidates = list(PREDICTION_DIR.glob("demand_forecast_*.csv"))
        if not candidates:
            candidates = list(OUTPUT_DIR.glob("demand_forecast_*.csv"))
        if not candidates:
            raise FileNotFoundError("수요 예측 파일이 없습니다. demand_predictor.py를 먼저 실행하세요.")
        path = max(candidates, key=lambda item: item.stat().st_mtime)
    else:
        path = Path(prediction_path)
        if not path.is_absolute():
            path = BASE_DIR / path

    data = read_csv_auto(path)
    required = ["기준_날짜", "대여소_ID", "기준_시간대", "대여예측건수", "반납예측건수"]
    missing = [col for col in required if col not in data.columns]
    if missing:
        raise ValueError(f"수요 예측 파일에 필요한 컬럼이 없습니다: {missing}")

    data = data[required].copy()
    data["기준_날짜"] = pd.to_datetime(data["기준_날짜"], errors="raise").dt.normalize()
    data["대여소_ID"] = data["대여소_ID"].astype("string").str.strip()
    data["기준_시간대"] = pd.to_numeric(data["기준_시간대"], errors="raise")
    for col in ["대여예측건수", "반납예측건수"]:
        data[col] = pd.to_numeric(data[col], errors="raise")

    if data.isna().any().any() or data["대여소_ID"].eq("").any():
        raise ValueError("수요 예측 파일에 결측값 또는 빈 정류소 ID가 있습니다.")
    if not data["기준_시간대"].between(0, 23).all() or not (data["기준_시간대"] % 1 == 0).all():
        raise ValueError("기준_시간대는 정수 0~23이어야 합니다.")
    if (data[["대여예측건수", "반납예측건수"]] < 0).any().any():
        raise ValueError("수요 예측값에 음수가 있습니다.")
    if data.duplicated(["기준_날짜", "대여소_ID", "기준_시간대"]).any():
        raise ValueError("중복된 날짜·정류소·시간 조합이 있습니다.")

    data["기준_시간대"] = data["기준_시간대"].astype(int)
    return data.sort_values(["기준_날짜", "기준_시간대", "대여소_ID"]).reset_index(drop=True)



def load_api_key() -> str | None:
    """환경변수 또는 .streamlit/secrets.toml에서 서울시 API 키를 읽는다."""
    key = os.getenv("SEOUL_OPENAPI_KEY")
    secrets_path = BASE_DIR / ".streamlit" / "secrets.toml"
    if not key and secrets_path.exists():
        with open(secrets_path, "rb") as file:
            key = tomllib.load(file).get("SEOUL_OPENAPI_KEY")
    return str(key).strip() if key else None


def fetch_station_snapshot(api_key: str, snapshot_time: pd.Timestamp, page_size: int = 1000, max_pages: int = 20) -> pd.DataFrame:
    """서울시 API에서 예측 시작 시각의 정류소별 재고를 조회한다."""
    try:
        import requests
    except ImportError as exc:
        raise ImportError("API 조회를 위해 requests 패키지가 필요합니다.") from exc

    station_dt = snapshot_time.strftime("%Y%m%d%H")
    base_url = f"http://openapi.seoul.go.kr:8088/{api_key}/json/bikeListHist"
    rows, seen_ids = [], set()

    for page in range(max_pages):
        start_idx = page * page_size + 1
        end_idx = start_idx + page_size - 1
        url = f"{base_url}/{start_idx}/{end_idx}/{station_dt}/"
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        service = response.json().get("getStationListHist")
        if service is None:
            raise ValueError("서울시 API 응답 형식이 예상과 다릅니다.")

        result = service.get("RESULT", {})
        code = result.get("CODE")
        if code == "INFO-200":
            break
        if code != "INFO-000":
            raise RuntimeError(f"서울시 API 오류 {code}: {result.get('MESSAGE')}")

        page_rows = service.get("row", [])
        new_rows = []
        for row in page_rows:
            station_id = str(row.get("stationId", "")).strip()
            if station_id and station_id not in seen_ids:
                seen_ids.add(station_id)
                new_rows.append(row)
        rows.extend(new_rows)
        if not page_rows or len(page_rows) < page_size or not new_rows:
            break

    if not rows:
        raise ValueError(f"{station_dt} 초기 재고를 서울시 API에서 가져오지 못했습니다.")

    snapshot = pd.DataFrame(rows).rename(columns={
        "stationId": "정류소_ID", "parkingBikeTotCnt": "현재재고"
    })
    snapshot["기준시각"] = snapshot_time
    return normalize_snapshot(snapshot)


def build_history_path(
    forecast_start: pd.Timestamp,
    output_path: str | Path | None = None
) -> Path:
    """예측 기준일에 대응하는 재고 이력 파일 경로를 만든다."""
    if output_path is not None:
        path = Path(output_path)
        return path if path.is_absolute() else BASE_DIR / path

    date = forecast_start.strftime("%Y%m%d")
    file_name = f"{date}_gangseo_station_history.csv"

    return HISTORY_DIR / file_name


def fetch_inventory_history(
    api_key: str,
    forecast_start: pd.Timestamp,
    target_station_ids: set[str],
    history_days: int = HISTORY_DAYS,
) -> pd.DataFrame:
    """예측 기준일 직전 N일과 기준일 00시 재고를 시간 단위로 수집한다.

    API 요청이 실패한 시간대와 조회 결과가 없는 정류소는 행을 만들지 않는다.
    실제로 조회된 재고만 이력에 저장한다.
    """
    history_start = forecast_start.normalize() - pd.Timedelta(days=history_days)
    initial_time = forecast_start.normalize()
    timestamps = pd.date_range(history_start, initial_time, freq="h")
    collected = []

    for timestamp in timestamps:
        try:
            snapshot = fetch_station_snapshot(api_key, timestamp)
        except ImportError:
            raise
        except Exception:
            # 해당 시각의 API 요청/응답 오류는 해당 시각의 관측 누락으로 취급한다.
            continue

        snapshot["정류소_ID"] = snapshot["정류소_ID"].astype(str).str.strip()
        snapshot = snapshot[snapshot["정류소_ID"].isin(target_station_ids)].copy()

        # 조회된 정류소가 없으면 빈 시간대이므로 저장하지 않는다.
        if snapshot.empty:
            continue

        snapshot["기준시각"] = timestamp
        collected.append(snapshot[["정류소_ID", "현재재고", "기준시각"]])

    if not collected:
        return pd.DataFrame({
            "정류소_ID": pd.Series(dtype="string"),
            "현재재고": pd.Series(dtype="int64"),
            "기준시각": pd.Series(dtype="datetime64[ns]"),
        })

    history = pd.concat(collected, ignore_index=True)
    history = normalize_snapshot(history)
    return history.sort_values(["기준시각", "정류소_ID"]).reset_index(drop=True)


def validate_inventory_history(
    history_df: pd.DataFrame,
    forecast_start: pd.Timestamp,
    target_station_ids: set[str],
    history_days: int = HISTORY_DAYS,
) -> None:
    """재고 이력의 필수 구조만 검증한다. 관측 시각·정류소 누락은 허용한다."""
    history = normalize_snapshot(history_df)
    if "기준시각" not in history.columns:
        raise ValueError("재고 이력에 '기준시각' 컬럼이 없습니다.")

    # API 누락으로 일부 날짜나 정류소 기록이 없어도 시뮬레이션은 계속한다.
    # 미관측 데이터는 이후 분위수 계산에서 자동으로 제외된다.
    return


def ensure_inventory_history(
    prediction_df: pd.DataFrame,
    history_path: str | Path | None = None,
    history_days: int = HISTORY_DAYS,
) -> tuple[pd.DataFrame, Path, str]:
    """기준일별 재고 이력을 파일에서 읽거나 API로 새로 수집한다."""
    first = prediction_df.sort_values(["기준_날짜", "기준_시간대"]).iloc[0]
    forecast_start = first["기준_날짜"] + pd.Timedelta(hours=int(first["기준_시간대"]))
    target_station_ids = set(prediction_df["대여소_ID"].astype(str).str.strip())
    path = build_history_path(forecast_start, history_path)

    if path.exists():
        history = load_inventory_history(path)
        validate_inventory_history(history, forecast_start, target_station_ids, history_days)
        return history, path, "기존 날짜별 재고 이력 파일"

    api_key = load_api_key()
    if not api_key:
        raise FileNotFoundError(
            f"재고 이력 파일이 없습니다: {path}\n"
            "파일이 없으면 서울시 API에서 기준일 직전 7일 + 기준일 00시를 수집해야 합니다. "
            ".streamlit/secrets.toml에 SEOUL_OPENAPI_KEY를 설정하세요."
        )

    history = fetch_inventory_history(api_key, forecast_start, target_station_ids, history_days)
    validate_inventory_history(history, forecast_start, target_station_ids, history_days)
    if not history.empty:
        path.parent.mkdir(parents=True, exist_ok=True)
        history.to_csv(path, index=False, encoding="utf-8-sig")
    return history, path, "서울시 API 신규 수집"


def load_inventory_snapshot_from_history(
    history_df: pd.DataFrame,
    forecast_start: pd.Timestamp,
) -> pd.DataFrame:
    """날짜별 재고 이력에서 기준일 00시 초기 재고를 분리한다."""
    initial = history_df[history_df["기준시각"] == forecast_start].copy()
    return initial[["정류소_ID", "현재재고", "기준시각"]].copy()


def prepare_initial_inventory(prediction_df: pd.DataFrame, snapshot_df: pd.DataFrame) -> pd.DataFrame:
    """예측 대상 정류소별 초기 재고를 구성한다. API 누락 정류소는 0으로 가정한다."""
    snapshot = normalize_snapshot(snapshot_df)[["정류소_ID", "현재재고"]]
    if snapshot["정류소_ID"].duplicated().any():
        raise ValueError("초기 재고 데이터에 중복 정류소가 있습니다.")

    station_ids = sorted(prediction_df["대여소_ID"].astype(str).str.strip().unique().tolist())
    snapshot = snapshot.rename(columns={"정류소_ID": "대여소_ID", "현재재고": "API재고"})
    initial = pd.DataFrame({"대여소_ID": station_ids}).merge(
        snapshot, on="대여소_ID", how="left", validate="one_to_one"
    )

    if (initial["API재고"].dropna() < 0).any():
        raise ValueError("음수인 API 초기 재고가 존재합니다.")

    initial["초기재고"] = np.floor(
        pd.to_numeric(initial["API재고"], errors="coerce").fillna(0).clip(lower=0) + 0.5
    ).astype(int)
    return initial[["대여소_ID", "초기재고"]]


def simulate_inventory(prediction_df: pd.DataFrame, initial_inventory_df: pd.DataFrame) -> pd.DataFrame:
    """대여를 먼저 처리하고 반납을 반영해 시간대별 재고를 계산한다."""
    pred = prediction_df.copy()
    pred["대여수요_정수"] = np.floor(pred["대여예측건수"].clip(lower=0) + 0.5).astype(int)
    pred["반납량_정수"] = np.floor(pred["반납예측건수"].clip(lower=0) + 0.5).astype(int)
    pred["_시뮬레이션시각"] = pred["기준_날짜"] + pd.to_timedelta(pred["기준_시간대"], unit="h")

    initial = initial_inventory_df.set_index("대여소_ID")["초기재고"].to_dict()
    station_ids = set(pred["대여소_ID"].astype(str))
    missing_ids = sorted(station_ids - set(initial))
    if missing_ids:
        raise ValueError(f"초기 재고가 없는 정류소가 있습니다: {missing_ids[:10]}")

    rows = []
    pred = pred.sort_values(["대여소_ID", "_시뮬레이션시각"])
    expected_times = tuple(sorted(pred["_시뮬레이션시각"].unique()))
    if not (pd.Series(expected_times).diff().dropna() == pd.Timedelta(hours=1)).all():
        raise ValueError("전체 예측 시간대가 연속적이지 않습니다.")

    for station_id, station_data in pred.groupby("대여소_ID", sort=False):
        times = tuple(station_data["_시뮬레이션시각"].tolist())
        if times != expected_times:
            raise ValueError(f"{station_id}의 예측 시간대 구성 또는 순서가 다른 정류소와 다릅니다.")

        stock = int(initial[station_id])
        for row in station_data.to_dict("records"):
            rental = int(row["대여수요_정수"])
            returns = int(row["반납량_정수"])
            expected_served_rental = min(rental, stock)
            ending_stock = stock - expected_served_rental + returns

            rows.append({
                "기준_날짜": row["기준_날짜"],
                "대여소_ID": station_id,
                "기준_시간대": row["기준_시간대"],
                "대여예측건수": row["대여예측건수"],
                "반납예측건수": row["반납예측건수"],
                "대여수요_정수": rental,
                "반납량_정수": returns,
                "예상_충족대여량": expected_served_rental,
                "시작재고": stock,
                "종료재고": ending_stock,
                "예상_미충족대여수요": rental - expected_served_rental,
                })
            stock = ending_stock

    return pd.DataFrame(rows).sort_values(
        ["기준_날짜", "대여소_ID", "기준_시간대"]
    ).reset_index(drop=True)


def select_recent_history(
    history_df: pd.DataFrame,
    forecast_start: pd.Timestamp,
    history_days: int = HISTORY_DAYS,
) -> pd.DataFrame:
    """예측 시작시각 직전 N일 범위에서 실제 관측된 재고만 선택한다."""
    window_start = forecast_start - pd.Timedelta(days=history_days)
    history = history_df[
        (history_df["기준시각"] >= window_start)
        & (history_df["기준시각"] < forecast_start)
    ].copy()
    return history.reset_index(drop=True)


def calculate_inventory_bounds(
    inventory_result: pd.DataFrame,
    history_df: pd.DataFrame,
    history_days: int = HISTORY_DAYS,
    min_stock: int = MIN_STOCK,
    min_range_width: int = MIN_RANGE_WIDTH,
) -> pd.DataFrame:
    """IFI로 목표 만족률을 정하고 과거 시간대 재고 분포로 상하한을 산출한다."""
    first = inventory_result.sort_values(["기준_날짜", "기준_시간대"]).iloc[0]
    forecast_start = first["기준_날짜"] + pd.Timedelta(hours=int(first["기준_시간대"]))
    history = select_recent_history(history_df, forecast_start, history_days)
    history["시간"] = history["기준시각"].dt.hour

    ifi = inventory_result.groupby(["기준_날짜", "대여소_ID"])["종료재고"].agg(
        예측최저재고="min", 예측최고재고="max"
    ).reset_index()
    ifi["IFI"] = ifi["예측최고재고"] - ifi["예측최저재고"]

    historical_ids = set(history["정류소_ID"].astype(str))
    eligible = ifi["대여소_ID"].astype(str).isin(historical_ids)
    ifi["IFI_등급"] = pd.Series(pd.NA, index=ifi.index, dtype="Int64")
    ifi["목표만족률(%)"] = np.nan

    for _, indexes in ifi.loc[eligible].groupby("기준_날짜").groups.items():
        rank = ifi.loc[indexes, "IFI"].rank(method="average", ascending=True, pct=True)
        grades = np.ceil(rank * 10).clip(1, 10).astype(int)
        ifi.loc[indexes, "IFI_등급"] = grades
        ifi.loc[indexes, "목표만족률(%)"] = 80 - 2 * (grades - 1)

    stock_groups = {
        key: np.sort(group["현재재고"].astype(int).to_numpy())
        for key, group in history.groupby(["정류소_ID", "시간"])
    }
    hours_by_date = {
        day: sorted(group["기준_시간대"].unique().tolist())
        for day, group in inventory_result.groupby("기준_날짜")
    }

    rows = []
    for item in ifi.to_dict("records"):
        station_id = str(item["대여소_ID"])
        satisfaction = item["목표만족률(%)"]
        for hour in hours_by_date[item["기준_날짜"]]:
            values = stock_groups.get((station_id, int(hour)), np.array([], dtype=int))
            count = len(values)
            raw_lower = raw_upper = lower = upper = np.nan

            if count and pd.notna(satisfaction):
                p_upper = (100 - satisfaction) / 300
                p_lower = 2 * (100 - satisfaction) / 300
                lower_idx = min(max(int(np.ceil(count * p_lower)) - 1, 0), count - 1)
                upper_idx = min(max(int(np.floor(count * (1 - p_upper))) - 1, 0), count - 1)
                raw_lower, raw_upper = int(values[lower_idx]), int(values[upper_idx])
                lower = max(min_stock, raw_lower)
                upper = max(raw_upper, lower + min_range_width)

            range_status = "예비 범위" if count > 0 and pd.notna(satisfaction) else "과거 관측 없음"
            rows.append({
                "기준_날짜": item["기준_날짜"], "대여소_ID": station_id, "기준_시간대": int(hour),
                "IFI": item["IFI"], "IFI_등급": item["IFI_등급"],
                "목표만족률(%)": satisfaction, "관측일수": count,
                "분포기반하한": raw_lower, "분포기반상한": raw_upper,
                "하한재고": lower, "상한재고": upper, "범위상태": range_status,
            })

    return pd.DataFrame(rows)


def create_operations_result(
    prediction_df: pd.DataFrame,
    history_df: pd.DataFrame,
    snapshot_df: pd.DataFrame,
    history_days: int = HISTORY_DAYS,
    min_stock: int = MIN_STOCK,
    min_range_width: int = MIN_RANGE_WIDTH,
) -> pd.DataFrame:
    """재고 시뮬레이션과 적정 재고 범위를 결합해 operations.py 입력을 만든다."""
    initial = prepare_initial_inventory(prediction_df, snapshot_df)
    inventory = simulate_inventory(prediction_df, initial)
    bounds = calculate_inventory_bounds(inventory, history_df, history_days, min_stock, min_range_width)
    result = inventory.merge(bounds, on=["기준_날짜", "대여소_ID", "기준_시간대"], how="left", validate="one_to_one")

    valid = result["범위상태"].eq("예비 범위")
    result["부족량"] = (result["하한재고"] - result["종료재고"]).clip(lower=0)
    result["초과량"] = (result["종료재고"] - result["상한재고"]).clip(lower=0)
    result["운영상태"] = "범위 산출 불가"
    result.loc[valid, "운영상태"] = "적정"
    result.loc[valid & (result["종료재고"] < result["하한재고"]), "운영상태"] = "공급부족"
    result.loc[valid & (result["종료재고"] > result["상한재고"]), "운영상태"] = "공급과잉"

    # 기존 노트북과 동일하게 날짜·시간대별 부족량/초과량 내림차순 dense rank를 부여한다.
    result["부족우선순위"] = np.nan
    result["초과우선순위"] = np.nan

    shortage_mask = result["운영상태"].eq("공급부족")
    surplus_mask = result["운영상태"].eq("공급과잉")

    shortage_rank = (
        result.loc[shortage_mask]
        .groupby(["기준_날짜", "기준_시간대"])["부족량"]
        .rank(method="dense", ascending=False)
    )
    surplus_rank = (
        result.loc[surplus_mask]
        .groupby(["기준_날짜", "기준_시간대"])["초과량"]
        .rank(method="dense", ascending=False)
    )
    result.loc[shortage_rank.index, "부족우선순위"] = shortage_rank
    result.loc[surplus_rank.index, "초과우선순위"] = surplus_rank

    return result[RESULT_COLUMNS].sort_values(
        ["기준_날짜", "기준_시간대", "대여소_ID"]
    ).reset_index(drop=True)


def run_operations_simulation(
    prediction_path: str | Path | None = None,
    history_path: str | Path | None = None,
    output_path: str | Path | None = None,
) -> pd.DataFrame:
    """모든 예측 대상 정류소의 수요예측·재고이력을 결합해 운영 결과를 생성한다."""
    prediction = load_prediction_data(prediction_path)
    if prediction.empty:
        raise ValueError("수요 예측 대상 정류소가 없습니다.")

    history, history_file, history_source = ensure_inventory_history(prediction, history_path)
    first = prediction.sort_values(["기준_날짜", "기준_시간대"]).iloc[0]
    forecast_start = first["기준_날짜"] + pd.Timedelta(hours=int(first["기준_시간대"]))
    snapshot = load_inventory_snapshot_from_history(history, forecast_start)
    result = create_operations_result(prediction, history, snapshot)

    if output_path is None:
        result_date = prediction["기준_날짜"].min().strftime("%Y%m%d")
        path = OPERATIONS_DIR / f"operations_result_{result_date}.csv"
    else:
        path = Path(output_path)
        if not path.is_absolute():
            path = BASE_DIR / path

    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    result.to_csv(temp_path, index=False, encoding="utf-8-sig")
    temp_path.replace(path)

    print("운영 시뮬레이션 완료")
    print(f"예측 기간: {result['기준_날짜'].min():%Y-%m-%d} ~ {result['기준_날짜'].max():%Y-%m-%d}")
    print(f"초기 재고 기준시각: {forecast_start:%Y-%m-%d %H:00}")
    print(f"재고 이력: {history_file} ({history_source})")
    print(f"정류소 수: {result['대여소_ID'].nunique():,}개 / 결과 행 수: {len(result):,}건")
    print(result["운영상태"].value_counts(dropna=False).to_string())
    print(f"저장 위치: {path}")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="운영 재고 시뮬레이션 실행")
    parser.add_argument("--prediction", default=None, help="수요 예측 CSV 경로")
    parser.add_argument("--history", default=None, help="날짜별 재고 이력 CSV 경로. 미지정 시 기준일별 파일을 자동 선택합니다.")
    parser.add_argument("--output", default=None, help="운영 결과 CSV 저장 경로. 미지정 시 outputs/operation_results/operations_result_기준일.csv")
    args = parser.parse_args()
    run_operations_simulation(args.prediction, args.history, args.output)


if __name__ == "__main__":
    main()
