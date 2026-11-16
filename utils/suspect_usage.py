"""날짜·정류소·시간대별 고장 의심 이용 기록 집계.

주의:
- OD 원천자료에는 개별 자전거 식별자가 없으므로 고장 차량의 고유 대수를 계산하지 않는다.
- 출발시간 기준 집계행의 전체_건수를 정류소·시간대별 고장 의심 건수로 집계한다.
- 도착시간 기준 집계는 동일 이동을 다시 집계한 결과일 수 있어 중복 방지를 위해 제외한다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st


REQUIRED_COLUMNS = [
    "기준_날짜",
    "집계_기준",
    "기준_시간대",
    "시작_대여소_ID",
    "전체_건수",
]


def get_suspect_candidates_signature(output_dir: str | Path) -> tuple[tuple[str, int, int], ...]:
    """후보 CSV의 경로·수정 시각·크기를 캐시 키로 반환한다."""
    folder = Path(output_dir)
    paths = sorted(folder.glob("suspect_usage_candidates_*.csv"))
    signature = []

    for path in paths:
        try:
            stat = path.stat()
        except OSError:
            continue
        signature.append((str(path), stat.st_mtime_ns, stat.st_size))

    return tuple(signature)


@st.cache_data(show_spinner=False)
def load_suspect_usage_hourly(
    target_date: str,
    source_signature: tuple[tuple[str, int, int], ...],
) -> tuple[pd.DataFrame, bool, str | None]:
    """후보 CSV에서 선택 날짜의 정류소·시간대별 의심 이용 건수를 집계한다.

    출발시간 기준 집계만 사용해 도착시간 기준 집계와의 중복 가능성을 피한다.
    반환 DataFrame은 대여소_ID, 기준_시간대, 점검의심이용건수 컬럼을 가지며, 정류소·시간대별로 합산된다.
    """
    empty = pd.DataFrame(columns=[
        "대여소_ID", "기준_시간대", "점검의심이용건수"
    ])
    if not source_signature:
        return empty, False, None

    target_day = pd.to_datetime(target_date, format="%Y%m%d", errors="coerce")
    if pd.isna(target_day):
        return empty, False, None

    # 최근 생성된 운영 파이프라인 후보 파일부터 확인한다.
    candidates = sorted(source_signature, key=lambda item: item[1], reverse=True)
    for file_path, _, _ in candidates:
        try:
            data = pd.read_csv(file_path, encoding="utf-8-sig", low_memory=False)
        except (OSError, UnicodeDecodeError, pd.errors.ParserError):
            continue

        data.columns = data.columns.astype(str).str.strip()
        if not set(REQUIRED_COLUMNS).issubset(data.columns):
            continue

        dates = pd.to_datetime(data["기준_날짜"], errors="coerce").dt.normalize()
        day_data = data.loc[dates.eq(target_day)].copy()
        if day_data.empty:
            continue

        # 동일 정류소 출발·도착 기록이 출발/도착 시각별로 각각 집계되어 있으므로
        # 출발시간 집계만 사용한다. 이 값은 고유 기기 수가 아니라 의심 이용 건수다.
        departure_data = day_data[
            day_data["집계_기준"].astype(str).str.strip().eq("출발시간")
        ].copy()
        if departure_data.empty:
            continue

        departure_data["대여소_ID"] = (
            departure_data["시작_대여소_ID"].astype("string").str.strip()
        )
        departure_data["기준_시간대"] = pd.to_numeric(
            departure_data["기준_시간대"], errors="coerce"
        )
        departure_data["전체_건수"] = pd.to_numeric(
            departure_data["전체_건수"], errors="coerce"
        )
        departure_data = departure_data.dropna(
            subset=["대여소_ID", "기준_시간대", "전체_건수"]
        )
        departure_data = departure_data[
            departure_data["기준_시간대"].between(0, 23)
        ]

        if departure_data.empty:
            return empty, True, Path(file_path).name

        result = (
            departure_data.groupby(["대여소_ID", "기준_시간대"], as_index=False)
            .agg(점검의심이용건수=("전체_건수", "sum"))
        )
        result["기준_시간대"] = result["기준_시간대"].astype(int)
        result["점검의심이용건수"] = result["점검의심이용건수"].round().astype(int)
        return result, True, Path(file_path).name

    return empty, False, None



def get_suspect_raw_source_signature(
    target_date: str,
    data_dir: str | Path,
    station_path: str | Path,
) -> tuple[tuple[str, int | None, int | None], ...]:
    """선택 날짜의 원본 OD와 정류소 마스터 파일 변경 여부를 캐시 키로 반환한다."""
    target_day = pd.to_datetime(target_date, format="%Y%m%d", errors="coerce")
    if pd.isna(target_day):
        return ()

    raw_path = Path(data_dir) / f"tpss_bcycl_od_statnhm_{target_day:%Y%m%d}.csv"
    paths = [raw_path, Path(station_path)]
    signature = []

    for path in paths:
        try:
            stat = path.stat()
            signature.append((str(path), stat.st_mtime_ns, stat.st_size))
        except OSError:
            signature.append((str(path), None, None))

    return tuple(signature)


@st.cache_data(show_spinner=False)
def load_suspect_usage_from_raw_od(
    target_date: str,
    source_signature: tuple[tuple[str, int | None, int | None], ...],
    data_dir: str | Path,
    station_path: str | Path,
    aggregation_basis: str = "출발시간",
) -> tuple[pd.DataFrame, bool, str | None]:
    """원본 OD를 직접 분석해 정류소·시간대별 고장 의심 이용 건수를 집계한다.

    운영 현황·대시보드는 출발시간/시작 정류소를 기본으로 사용한다.
    직접 재배치에서는 별도 호출로 도착시간/종료 정류소를 사용할 수 있다.
    """
    empty = pd.DataFrame(columns=[
        "대여소_ID", "기준_시간대", "점검의심이용건수"
    ])
    if aggregation_basis not in {"출발시간", "도착시간"}:
        raise ValueError("aggregation_basis는 '출발시간' 또는 '도착시간'이어야 합니다.")

    target_day = pd.to_datetime(target_date, format="%Y%m%d", errors="coerce")
    if pd.isna(target_day):
        return empty, False, f"잘못된 기준 날짜: {target_date}"

    raw_path = Path(data_dir) / f"tpss_bcycl_od_statnhm_{target_day:%Y%m%d}.csv"
    station_path = Path(station_path)
    if not raw_path.is_file():
        return empty, False, f"원본 OD 파일 없음: {raw_path}"
    if not station_path.is_file():
        return empty, False, f"정류소 마스터 파일 없음: {station_path}"

    try:
        # 모델 학습 파이프라인과 동일한 인코딩·강서구 정류소 필터를 사용한다.
        from utils.daily_model_pipeline import load_gangseo_station_ids, read_csv_auto

        station_ids = set(
            pd.Series(load_gangseo_station_ids(station_path), dtype="string")
            .astype("string").str.strip().str.replace(r"\.0$", "", regex=True)
        )
        data = read_csv_auto(
            raw_path,
            dtype={"시작_대여소": "string", "종료_대여소": "string"},
        )
        data.columns = data.columns.astype(str).str.strip()
        data = data.rename(columns={
            "기준_시간": "기준_시간대",
            "시작_대여소": "시작_대여소_ID",
            "종료_대여소": "종료_대여소_ID",
            "전체건수": "전체_건수",
        })

        required = {
            "기준_시간대", "집계_기준", "시작_대여소_ID", "종료_대여소_ID",
            "전체_건수", "전체_이용_분", "전체_이용_거리",
        }
        missing = required.difference(data.columns)
        if missing:
            raise ValueError(f"원본 OD 필수 컬럼 누락: {sorted(missing)}")

        if "기준_날짜" in data.columns:
            date_keys = (
                data["기준_날짜"].astype("string").str.strip()
                .str.replace(r"\.0$", "", regex=True)
                .str.replace(r"\D", "", regex=True).str.slice(0, 8)
            )
            if date_keys.notna().any() and date_keys.ne("").any():
                matched = date_keys.eq(target_day.strftime("%Y%m%d"))
                if matched.any():
                    data = data.loc[matched].copy()
                elif date_keys.str.len().eq(8).any():
                    raise ValueError(
                        f"원본 OD 내부 날짜가 선택 날짜 {target_day:%Y-%m-%d}와 다릅니다."
                    )

        def normalize_ids(values: pd.Series) -> pd.Series:
            return (
                values.astype("string").str.strip()
                .str.replace(r"\.0$", "", regex=True)
            )

        start_ids = normalize_ids(data["시작_대여소_ID"])
        end_ids = normalize_ids(data["종료_대여소_ID"])
        gangseo_mask = start_ids.isin(station_ids) & end_ids.isin(station_ids)
        gangseo_data = data.loc[gangseo_mask].copy()
        gangseo_data["시작_대여소_ID"] = start_ids.loc[gangseo_mask]
        gangseo_data["종료_대여소_ID"] = end_ids.loc[gangseo_mask]

        duration = pd.to_numeric(gangseo_data["전체_이용_분"], errors="coerce")
        distance = pd.to_numeric(gangseo_data["전체_이용_거리"], errors="coerce")
        suspect_mask = (
            gangseo_data["시작_대여소_ID"].eq(gangseo_data["종료_대여소_ID"])
            & duration.isna()
            & (distance.isna() | distance.lt(100))
            & gangseo_data["종료_대여소_ID"].ne("X")
        )
        suspect_data = gangseo_data.loc[suspect_mask].copy()
        suspect_data["집계_기준"] = suspect_data["집계_기준"].astype("string").str.strip()
        suspect_data = suspect_data.loc[
            suspect_data["집계_기준"].eq(aggregation_basis)
        ].copy()

        raw_time = pd.to_numeric(suspect_data["기준_시간대"], errors="coerce")
        hour_values = raw_time.where(raw_time.le(23), raw_time // 100)
        suspect_data["기준_시간대"] = hour_values
        suspect_data["전체_건수"] = pd.to_numeric(
            suspect_data["전체_건수"], errors="coerce"
        )
        station_column = (
            "시작_대여소_ID" if aggregation_basis == "출발시간" else "종료_대여소_ID"
        )
        suspect_data = suspect_data.dropna(
            subset=[station_column, "기준_시간대", "전체_건수"]
        )
        suspect_data = suspect_data.loc[
            suspect_data["기준_시간대"].between(0, 23)
            & suspect_data["전체_건수"].gt(0)
        ]

        if suspect_data.empty:
            return empty, True, (
                f"원본 OD 직접 집계: {raw_path.name} | 강서구 원천 {len(gangseo_data):,}행 | "
                f"{aggregation_basis} 의심 기록 0행"
            )

        result = (
            suspect_data.groupby([station_column, "기준_시간대"], as_index=False)
            .agg(점검의심이용건수=("전체_건수", "sum"))
            .rename(columns={station_column: "대여소_ID"})
        )
        result["대여소_ID"] = normalize_ids(result["대여소_ID"])
        result["기준_시간대"] = pd.to_numeric(
            result["기준_시간대"], errors="coerce"
        ).astype(int)
        result["점검의심이용건수"] = (
            pd.to_numeric(result["점검의심이용건수"], errors="coerce")
            .fillna(0).round().astype(int)
        )
        source_name = (
            f"원본 OD 직접 집계: {raw_path.name} | 강서구 원천 {len(gangseo_data):,}행 | "
            f"의심 조건 충족 {int(suspect_mask.sum()):,}행 | {aggregation_basis}"
        )
        return result, True, source_name

    except Exception as error:
        return empty, False, f"원본 OD 분석 실패: {type(error).__name__}: {error}"
