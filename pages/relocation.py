import os
import json
import hashlib
from pathlib import Path
import pandas as pd

from io import BytesIO
from html import escape

import streamlit as st
import streamlit.components.v1 as components

from utils.relocation_planner import (
    create_relocation_plan,
    get_available_dates,
    get_available_hours,
    get_result_path,
    get_promotion_result_signature,
    assign_vehicles,
    build_vehicle_visit_plan,
    load_station_master,
    haversine_distance,
)

from utils.route_api import get_vehicle_route
from utils.relocation_settings import load_relocation_settings
from utils.operations_assistant import render_operations_assistant
from utils.page_header import render_page_title
# 직접 재배치 점검 후보는 날짜별 원천 CSV를 직접 조회한다.
# 대시보드·운영 현황에서 사용하는 출발시간 기본 집계와 분리한다.

# --------------------------------
# 화면 설정값
# --------------------------------

st.markdown("""
<style>
.kpi-card {
    background-color: var(--card-bg);
    color: white;
    border-radius: 10px;
    padding: 18px;
    min-height: 105px;
    box-shadow: 0 2px 5px rgba(0, 0, 0, 0.12);
}

.kpi-card * {
    color: white !important;
}

.kpi-label {
    font-size: 14px;
    opacity: 0.9;
}

.kpi-value {
    font-size: 28px;
    font-weight: 700;
    margin-top: 8px;
}

/* 작업 배치 조정 옆 종 아이콘: 마우스 오버 시 안내 표시 */
.inspection-bell-wrap {
    display: flex;
    justify-content: flex-start;
    align-items: center;
    overflow: visible !important;
    min-height: 2rem;
}

.inspection-bell-tooltip {
    position: relative;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 3rem;
    height: 2.8rem;
    font-size: 1.5rem;
    line-height: 1;
    cursor: help;
    z-index: 20;
}

.inspection-bell-tooltip::after {
    content: attr(data-tooltip);
    position: absolute;
    top: calc(100% + 0.45rem);
    right: 0;
    width: max-content;
    max-width: 340px;
    padding: 0.65rem 0.8rem;
    border: 1px solid var(--st-border-color);
    border-radius: 0.5rem;
    background: var(--secondary-background-color);
    color: var(--text-color);
    box-shadow: 0 4px 14px rgba(0, 0, 0, 0.16);
    font-size: 0.82rem;
    font-weight: 400;
    line-height: 1.45;
    white-space: normal;
    text-align: left;
    visibility: hidden;
    opacity: 0;
    pointer-events: none;
    transition: opacity 0.12s ease;
    top: 50%;
    left: calc(100% + 0.5rem);
    right: auto;
    bottom: auto;
    transform: translateY(-50%);
}

.inspection-bell-tooltip:hover::after,
.inspection-bell-tooltip:focus::after {
    visibility: visible;
    opacity: 1;
}
.inspection-bell-wrap {
    display: flex;
    justify-content: flex-start;
    align-items: center;
    margin-left: -0.5rem;
    margin-top: 0.35rem;
}
.work-adjustment-title-row {
    display: flex;
    align-items: center;
    gap: 0.35rem;
}

.work-adjustment-title-row h3 {
    margin: 0 !important;
    font-size: 1.75rem;
    font-weight: 600;
    line-height: 1.3;
}
</style>
""", unsafe_allow_html=True)

def create_work_order_excel(assigned_plan_df, visit_df):
    """차량별 작업지시서와 전체 매칭 결과를 Excel로 저장한다."""

    visit_df = visit_df.copy()
    if "작업설명" not in visit_df.columns:
        visit_df["작업설명"] = ""
    else:
        visit_df["작업설명"] = visit_df["작업설명"].fillna("")
    if "고장 의심 이용 건수(건)" not in visit_df.columns:
        visit_df["고장 의심 이용 건수(건)"] = 0
    else:
        visit_df["고장 의심 이용 건수(건)"] = pd.to_numeric(
            visit_df["고장 의심 이용 건수(건)"], errors="coerce"
        ).fillna(0).astype(int)

    output = BytesIO()

    visit_columns = [
        "방문순서",
        "방문유형",
        "정류소_ID",
        "정류소명",
        "작업수량",
        "상대정류소명",
        "묶음거리_m",
        "작업설명",
        "고장 의심 이용 건수(건)",
    ]

    with pd.ExcelWriter(output, engine="openpyxl") as writer:

        for vehicle_number in [1, 2]:

            vehicle_data = visit_df[
                visit_df["차량번호"] == vehicle_number
            ].copy()

            vehicle_data = vehicle_data.sort_values("방문순서")
            if "방문유형" in vehicle_data.columns and "작업수량" in vehicle_data.columns:
                vehicle_data.loc[
                    vehicle_data["방문유형"].eq("점검 방문"), "작업수량"
                ] = None

            available_columns = [
                col for col in visit_columns
                if col in vehicle_data.columns
            ]

            vehicle_data[available_columns].to_excel(
                writer,
                sheet_name=f"차량{vehicle_number}",
                index=False,
            )

        # 관리자 확인용 전체 매칭 결과
        assigned_plan_df.to_excel(
            writer,
            sheet_name="전체매칭",
            index=False,
        )

        # 엑셀 기본 서식
        for worksheet in writer.book.worksheets:
            worksheet.freeze_panes = "A2"
            worksheet.auto_filter.ref = worksheet.dimensions

            for column in worksheet.columns:
                max_length = max(
                    len(str(cell.value or ""))
                    for cell in column
                )

                worksheet.column_dimensions[
                    column[0].column_letter
                ].width = min(max(max_length + 3, 12), 32)

    return output.getvalue()

# ==================================================
# 1. API 키 및 공통 함수
# ==================================================

def get_kakao_api_key():
    api_key = os.getenv("KAKAO_MOBILITY_REST_API_KEY", "").strip()

    if api_key:
        return api_key

    try:
        return str(
            st.secrets["KAKAO_MOBILITY_REST_API_KEY"]
        ).strip()
    except Exception:
        return ""


def get_kakao_map_js_key():
    js_key = os.getenv("KAKAO_MAP_JAVASCRIPT_KEY", "").strip()

    if js_key:
        return js_key

    try:
        return str(
            st.secrets["KAKAO_MAP_JAVASCRIPT_KEY"]
        ).strip()
    except Exception:
        return ""


def normalize_station_ids(values):
    return (
        values.astype("string")
        .str.strip()
        .str.replace(r"\.0$", "", regex=True)
    )


def read_csv_with_fallback(file_path):
    """프로젝트에서 사용하는 CSV 인코딩 순서로 읽는다."""
    last_error = None
    for encoding in ("utf-8-sig", "cp949", "utf-8"):
        try:
            return pd.read_csv(file_path, encoding=encoding, low_memory=False)
        except UnicodeDecodeError as error:
            last_error = error
    raise UnicodeError(f"CSV 인코딩을 확인할 수 없습니다: {file_path}") from last_error


def normalize_date_key(values):
    """20261107, 2026-11-07 등 날짜 표현을 YYYYMMDD로 통일한다."""
    return (
        values.astype("string")
        .str.strip()
        .str.replace(r"\.0$", "", regex=True)
        .str.replace(r"\D", "", regex=True)
        .str.slice(0, 8)
    )


def load_gangseo_station_reference(project_root):
    """모델 파이프라인과 동일하게 좌표가 유효한 강서구 정류소 ID를 반환한다."""
    station_path = project_root / "data" / "서울시 따릉이대여소 마스터 정보.csv"
    if not station_path.is_file():
        raise FileNotFoundError(f"정류소 마스터 파일을 찾을 수 없습니다: {station_path}")

    station = read_csv_with_fallback(station_path)
    station.columns = station.columns.astype(str).str.strip()
    required = {"대여소_ID", "주소1", "위도"}
    missing = required.difference(station.columns)
    if missing:
        raise ValueError(f"정류소 마스터 필수 컬럼 누락: {sorted(missing)}")

    station["대여소_ID"] = normalize_station_ids(station["대여소_ID"])
    latitude = pd.to_numeric(station["위도"], errors="coerce")
    mask = (
        station["주소1"].astype("string").str.contains("강서구", na=False)
        & latitude.notna()
        & latitude.ne(0)
        & station["대여소_ID"].notna()
    )
    station = station.loc[mask].copy()
    if station.empty:
        raise ValueError("정류소 마스터에서 유효한 강서구 정류소를 찾지 못했습니다.")

    name_column = next(
        (column for column in ("대여소명", "대여소_명", "정류소명", "대여소명칭")
         if column in station.columns),
        None,
    )
    if name_column:
        station_names = station[["대여소_ID", name_column]].rename(
            columns={name_column: "대여소명"}
        )
    else:
        station_names = station[["대여소_ID"]].copy()
        station_names["대여소명"] = station_names["대여소_ID"]

    station_names = station_names.drop_duplicates("대여소_ID")
    station_ids = set(station_names["대여소_ID"].dropna().astype(str))
    return station_ids, station_names, station_path


def derive_inspection_candidates_from_raw_od(
    raw_path,
    target_date,
    target_hour,
    aggregation_basis,
    station_ids,
    station_names,
):
    """원본 OD에 daily_model_pipeline과 같은 의심 조건을 적용해 후보를 생성한다."""
    data = read_csv_with_fallback(raw_path)
    data.columns = data.columns.astype(str).str.strip()
    data = data.rename(columns={
        "기준_시간": "기준_시간대",
        "시작_대여소": "시작_대여소_ID",
        "종료_대여소": "종료_대여소_ID",
        "전체건수": "전체_건수",
    })

    required = {
        "집계_기준", "기준_시간대", "시작_대여소_ID", "종료_대여소_ID",
        "전체_건수", "전체_이용_분", "전체_이용_거리",
    }
    missing = required.difference(data.columns)
    if missing:
        raise ValueError(f"원본 OD 필수 컬럼 누락: {sorted(missing)}")

    target_date_key = pd.to_datetime(target_date).strftime("%Y%m%d")
    if "기준_날짜" in data.columns:
        date_keys = normalize_date_key(data["기준_날짜"])
        date_filtered = data.loc[date_keys.eq(target_date_key)].copy()
        # 파일명이 해당 날짜로 특정되어 있고 날짜 컬럼 값이 파싱 불가능할 때만 파일 날짜를 신뢰한다.
        if not date_filtered.empty:
            data = date_filtered
        elif date_keys.notna().any():
            raise ValueError(
                f"원본 OD 내부 기준_날짜가 선택 날짜 {target_date_key}와 일치하지 않습니다."
            )

    start_ids = normalize_station_ids(data["시작_대여소_ID"])
    end_ids = normalize_station_ids(data["종료_대여소_ID"])
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
    # 원본 OD는 HHMM(예: 1415), 후보 CSV는 0~23시로 저장되므로 두 형식 모두 처리한다.
    hour_values = raw_time.where(raw_time.le(23), raw_time // 100)
    suspect_data["_집계시간"] = hour_values
    suspect_data["전체_건수"] = pd.to_numeric(
        suspect_data["전체_건수"], errors="coerce"
    )

    station_column = (
        "시작_대여소_ID" if aggregation_basis == "출발시간" else "종료_대여소_ID"
    )
    selected = suspect_data.loc[
        suspect_data["_집계시간"].eq(int(target_hour))
    ].copy()
    selected = selected.dropna(subset=[station_column, "전체_건수"])
    selected = selected.loc[selected["전체_건수"].gt(0)]

    if selected.empty:
        empty = pd.DataFrame(columns=[
            "대여소_ID", "대여소명", "고장 의심 이용 건수(건)"
        ])
        source_text = (
            f"원본 OD 직접 분석: {raw_path} | 강서구 원천 {len(gangseo_data):,}행 | "
            f"의심 조건 충족 {len(suspect_data):,}행 | {aggregation_basis} "
            f"{int(target_hour):02d}시 0행"
        )
        return empty, source_text, len(gangseo_data), len(suspect_data), 0

    aggregated = (
        selected.groupby(station_column, as_index=False)
        .agg(**{"고장 의심 이용 건수(건)": ("전체_건수", "sum")})
        .rename(columns={station_column: "대여소_ID"})
    )
    aggregated["대여소_ID"] = normalize_station_ids(aggregated["대여소_ID"])
    aggregated = aggregated.merge(
        station_names,
        on="대여소_ID",
        how="left",
        validate="many_to_one",
    )
    aggregated["대여소명"] = aggregated["대여소명"].fillna(aggregated["대여소_ID"])
    aggregated["고장 의심 이용 건수(건)"] = (
        pd.to_numeric(aggregated["고장 의심 이용 건수(건)"], errors="coerce")
        .fillna(0)
        .round()
        .astype(int)
    )
    candidates = aggregated[[
        "대여소_ID", "대여소명", "고장 의심 이용 건수(건)"
    ]].sort_values(
        ["고장 의심 이용 건수(건)", "대여소명"],
        ascending=[False, True],
    ).reset_index(drop=True)

    source_text = (
        f"원본 OD 직접 분석: {raw_path} | 강서구 원천 {len(gangseo_data):,}행 | "
        f"의심 조건 충족 {len(suspect_data):,}행 | {aggregation_basis} "
        f"{int(target_hour):02d}시 {len(selected):,}행"
    )
    return candidates, source_text, len(gangseo_data), len(suspect_data), len(selected)


def load_inspection_candidates(target_date, target_hour, aggregation_basis="도착시간"):
    """원본 일별 OD를 우선 분석하고, 원본이 없을 때 후보 CSV를 보조적으로 사용한다."""
    project_root = Path(__file__).resolve().parents[1]
    target_day = pd.to_datetime(target_date, errors="coerce")
    empty = pd.DataFrame(columns=[
        "대여소_ID", "대여소명", "고장 의심 이용 건수(건)"
    ])
    if pd.isna(target_day):
        return empty, False, "잘못된 기준 날짜", ()

    target_date_key = target_day.strftime("%Y%m%d")
    target_hour = int(target_hour)
    raw_path = (
        project_root / "data" / "daily_data"
        / f"tpss_bcycl_od_statnhm_{target_date_key}.csv"
    )
    station_path = project_root / "data" / "서울시 따릉이대여소 마스터 정보.csv"

    source_signature = []
    for path in (raw_path, station_path):
        if path.is_file():
            stat = path.stat()
            source_signature.append((str(path), stat.st_mtime_ns, stat.st_size))
    source_signature = tuple(source_signature)

    # 1순위: 매 날짜별 원천 OD를 직접 분석한다. 후보 CSV 생성 여부에 의존하지 않는다.
    if raw_path.is_file():
        try:
            station_ids, station_names, _ = load_gangseo_station_reference(project_root)
            candidates, source_text, _, _, _ = derive_inspection_candidates_from_raw_od(
                raw_path,
                target_day,
                target_hour,
                aggregation_basis,
                station_ids,
                station_names,
            )
            return candidates, True, source_text, source_signature
        except Exception as error:
            raw_error = f"원본 OD 분석 실패: {type(error).__name__}: {error}"
    else:
        raw_error = f"원본 OD 파일 없음: {raw_path}"

    # 2순위: 일별 모델 파이프라인이 생성한 후보 CSV가 있는 경우 보조 사용한다.
    output_root = project_root / "outputs"
    candidate_path = output_root / f"suspect_usage_candidates_{target_date_key}.csv"
    if candidate_path.is_file():
        try:
            data = read_csv_with_fallback(candidate_path)
            data.columns = data.columns.astype(str).str.strip()
            station_column = (
                "시작_대여소_ID" if aggregation_basis == "출발시간" else "종료_대여소_ID"
            )
            required = {
                "기준_날짜", "집계_기준", "기준_시간대", station_column, "전체_건수"
            }
            missing = required.difference(data.columns)
            if missing:
                raise ValueError(f"후보 CSV 필수 컬럼 누락: {sorted(missing)}")

            date_keys = normalize_date_key(data["기준_날짜"])
            data = data.loc[date_keys.eq(target_date_key)].copy()
            data = data.loc[
                data["집계_기준"].astype("string").str.strip().eq(aggregation_basis)
            ].copy()
            times = pd.to_numeric(data["기준_시간대"], errors="coerce")
            data = data.loc[times.eq(target_hour)].copy()
            data["대여소_ID"] = normalize_station_ids(data[station_column])
            data["전체_건수"] = pd.to_numeric(data["전체_건수"], errors="coerce")
            data = data.dropna(subset=["대여소_ID", "전체_건수"])
            if not data.empty:
                name_column = (
                    "시작_대여소명" if aggregation_basis == "출발시간" else "종료_대여소명"
                )
                name_map = (
                    data[["대여소_ID", name_column]].drop_duplicates("대여소_ID")
                    .rename(columns={name_column: "대여소명"})
                    if name_column in data.columns
                    else data[["대여소_ID"]].assign(대여소명=lambda frame: frame["대여소_ID"])
                )
                grouped = data.groupby("대여소_ID", as_index=False).agg(
                    **{"고장 의심 이용 건수(건)": ("전체_건수", "sum")}
                )
                grouped = grouped.merge(name_map, on="대여소_ID", how="left")
                grouped["대여소명"] = grouped["대여소명"].fillna(grouped["대여소_ID"])
                result = grouped[[
                    "대여소_ID", "대여소명", "고장 의심 이용 건수(건)"
                ]].sort_values(
                    ["고장 의심 이용 건수(건)", "대여소명"],
                    ascending=[False, True],
                ).reset_index(drop=True)
                stat = candidate_path.stat()
                source_signature += ((str(candidate_path), stat.st_mtime_ns, stat.st_size),)
                return result, True, f"후보 CSV 보조 조회: {candidate_path}", source_signature
            return empty, True, f"후보 CSV는 있으나 {aggregation_basis} {target_hour:02d}시 행이 없습니다: {candidate_path}", source_signature
        except Exception as error:
            raw_error += f" | 후보 CSV 분석 실패: {type(error).__name__}: {error}"

    return empty, False, raw_error, source_signature


# ==================================================
# 2. 방문 정류소 좌표 확인
# ==================================================

def attach_station_coordinates(visit_df):
    visits = visit_df.copy()

    # 컬럼명 통일
    rename_map = {"대여소_ID": "정류소_ID","대여소명": "정류소명","정류소_위도": "위도","정류소_경도": "경도"}

    for old_name, new_name in rename_map.items():
        if old_name in visits.columns and new_name not in visits.columns:
            visits = visits.rename(columns={old_name: new_name})

    required = ["차량번호","방문순서","방문유형","정류소_ID","정류소명","작업수량"]

    missing = [col for col in required if col not in visits.columns]

    if missing:
        raise ValueError(f"방문 작업 데이터에 필요한 컬럼이 없습니다: {missing}")

    visits["정류소_ID"] = normalize_station_ids(visits["정류소_ID"])

    # 기존 방문 데이터에 좌표가 없거나 비어 있는 경우
    # 정류소 마스터에서 좌표 연결
    need_coordinates = ("위도" not in visits.columns or "경도" not in visits.columns)

    if not need_coordinates:
        need_coordinates = visits[["위도", "경도"]].isna().any().any()

    if need_coordinates:
        visits = visits.drop(columns=["위도", "경도"],errors="ignore")

        stations = load_station_master()

        stations["대여소_ID"] = normalize_station_ids(stations["대여소_ID"])

        stations = stations[["대여소_ID", "위도", "경도"]]

        visits = visits.merge(stations,left_on="정류소_ID",right_on="대여소_ID",how="left")

        visits = visits.drop(columns=["대여소_ID"],errors="ignore")

    if visits[["위도", "경도"]].isna().any().any():
        raise ValueError("정류소 마스터에서 일부 정류소의 좌표를 찾지 못했습니다.")

    visits["위도"] = visits["위도"].astype(float)
    visits["경도"] = visits["경도"].astype(float)

    return visits


def add_inspection_visits_to_route(base_visit_df, inspection_candidates):
    """고장 의심 정류소를 추가 이동거리가 가장 적은 차량 경로에 자동 삽입한다.

    기존 재배치 방문은 유지한다. 이미 방문하는 정류소는 해당 방문에 점검 정보를
    합치고, 신규 정류소는 두 차량의 모든 삽입 위치 중 직선거리 증가량이 가장
    작은 위치에 추가한다. 고장 의심 건수를 회수 대수로 변환하지 않는다.
    """
    visits = base_visit_df.copy()
    if "작업설명" not in visits.columns:
        visits["작업설명"] = ""
    else:
        visits["작업설명"] = visits["작업설명"].fillna("").astype("object")

    if "고장 의심 이용 건수(건)" not in visits.columns:
        visits["고장 의심 이용 건수(건)"] = 0
    visits["고장 의심 이용 건수(건)"] = pd.to_numeric(
        visits["고장 의심 이용 건수(건)"], errors="coerce"
    ).fillna(0).astype(int)

    if inspection_candidates is None or inspection_candidates.empty:
        return visits.sort_values(["차량번호", "방문순서"]).reset_index(drop=True)

    candidates = inspection_candidates.copy()
    required_candidate_columns = {
        "대여소_ID", "대여소명", "고장 의심 이용 건수(건)"
    }
    missing = required_candidate_columns.difference(candidates.columns)
    if missing:
        raise ValueError(f"점검 방문 후보 데이터 필수 컬럼 누락: {sorted(missing)}")

    candidates["대여소_ID"] = normalize_station_ids(candidates["대여소_ID"])
    candidates["고장 의심 이용 건수(건)"] = pd.to_numeric(
        candidates["고장 의심 이용 건수(건)"], errors="coerce"
    ).fillna(0).astype(int)
    candidates = candidates.dropna(subset=["대여소_ID"])
    candidates = candidates.loc[candidates["고장 의심 이용 건수(건)"].gt(0)]
    candidates = candidates.groupby("대여소_ID", as_index=False).agg(
        대여소명=("대여소명", "first"),
        **{"고장 의심 이용 건수(건)": ("고장 의심 이용 건수(건)", "sum")},
    )

    if candidates.empty:
        return visits.sort_values(["차량번호", "방문순서"]).reset_index(drop=True)

    stations = load_station_master().copy()
    stations["대여소_ID"] = normalize_station_ids(stations["대여소_ID"])
    station_lookup = (
        stations.drop_duplicates("대여소_ID")
        .set_index("대여소_ID")
        .to_dict("index")
    )

    task_items = []
    missing_coordinates = []
    for candidate in candidates.to_dict("records"):
        station_id = str(candidate["대여소_ID"])
        station = station_lookup.get(station_id)
        if station is None or pd.isna(station.get("위도")) or pd.isna(station.get("경도")):
            missing_coordinates.append(station_id)
            continue
        candidate_name = candidate.get("대여소명")
        if pd.isna(candidate_name) or not str(candidate_name).strip():
            candidate_name = station.get("대여소명", station_id)
        task_items.append({
            "station_id": station_id,
            "station_name": str(candidate_name),
            "suspect_count": int(candidate["고장 의심 이용 건수(건)" ]),
            "lat": float(station["위도"]),
            "lng": float(station["경도"]),
        })

    if missing_coordinates:
        raise ValueError(
            "정류소 마스터에서 점검 방문 후보의 좌표를 찾지 못했습니다: "
            + ", ".join(missing_coordinates)
        )

    # 의심 기록이 많은 정류소부터 삽입해 주요 후보를 먼저 경로에 반영한다.
    task_items.sort(key=lambda item: (-item["suspect_count"], item["station_name"]))

    vehicle_frames = {
        vehicle_number: visits.loc[visits["차량번호"].eq(vehicle_number)]
        .sort_values("방문순서").copy().reset_index(drop=True)
        for vehicle_number in (1, 2)
    }

    def insertion_cost(route_frame, task, position):
        """방문 순서에 신규 정류소를 삽입할 때 늘어나는 직선거리(m)."""
        if route_frame.empty:
            return 0.0
        if position == 0:
            first = route_frame.iloc[0]
            return haversine_distance(
                task["lat"], task["lng"], float(first["위도"]), float(first["경도"])
            )
        if position == len(route_frame):
            last = route_frame.iloc[-1]
            return haversine_distance(
                float(last["위도"]), float(last["경도"]), task["lat"], task["lng"]
            )

        previous = route_frame.iloc[position - 1]
        following = route_frame.iloc[position]
        return (
            haversine_distance(
                float(previous["위도"]), float(previous["경도"]), task["lat"], task["lng"]
            )
            + haversine_distance(
                task["lat"], task["lng"], float(following["위도"]), float(following["경도"])
            )
            - haversine_distance(
                float(previous["위도"]), float(previous["경도"]),
                float(following["위도"]), float(following["경도"]),
            )
        )

    original_active_vehicles = [
        vehicle_number
        for vehicle_number, route_frame in vehicle_frames.items()
        if not route_frame.empty
    ]
    eligible_vehicles = original_active_vehicles or [1, 2]

    for task in task_items:
        # 기존 방문 정류소라면 새 정류소를 만들지 않고 기존 방문에 점검 정보를 결합한다.
        existing_options = []
        for vehicle_number, route_frame in vehicle_frames.items():
            matches = route_frame.index[
                route_frame["정류소_ID"].astype(str).eq(task["station_id"])
            ].tolist()
            if matches:
                existing_options.append((len(route_frame), vehicle_number, matches[0]))

        if existing_options:
            _, vehicle_number, existing_index = min(existing_options)
            route_frame = vehicle_frames[vehicle_number].copy()
            current_value = route_frame.at[existing_index, "작업설명"]
            current_text = "" if pd.isna(current_value) else str(current_value).strip()
            inspection_text = "고장 의심 정류소 현장 점검"
            if inspection_text not in current_text:
                route_frame.at[existing_index, "작업설명"] = (
                    f"{current_text} · {inspection_text}" if current_text else inspection_text
                )
            route_frame.at[existing_index, "고장 의심 이용 건수(건)"] = max(
                int(route_frame.at[existing_index, "고장 의심 이용 건수(건)"]),
                task["suspect_count"],
            )
            vehicle_frames[vehicle_number] = route_frame
            continue

        # 두 차량 경로의 모든 삽입 위치를 비교해 추가 이동량이 최소인 곳을 선택한다.
        insertion_options = []
        for vehicle_number in eligible_vehicles:
            route_frame = vehicle_frames[vehicle_number]
            for position in range(len(route_frame) + 1):
                extra_distance = insertion_cost(route_frame, task, position)
                insertion_options.append((extra_distance, len(route_frame), vehicle_number, position))

        _, _, vehicle_number, insert_at = min(insertion_options)
        route_frame = vehicle_frames[vehicle_number].copy()
        new_row = {column: pd.NA for column in route_frame.columns}
        new_row.update({
            "차량번호": vehicle_number,
            "방문순서": 0,
            "작업묶음_ID": f"INS-{task['station_id']}",
            "방문유형": "점검 방문",
            "정류소_ID": task["station_id"],
            "정류소명": task["station_name"],
            "작업수량": 0,
            "위도": task["lat"],
            "경도": task["lng"],
            "상대정류소명": "",
            "묶음거리_m": 0,
            "작업설명": "고장 의심 기록 현장 확인",
            "고장 의심 이용 건수(건)": task["suspect_count"],
        })
        task_frame = pd.DataFrame([new_row], columns=route_frame.columns)
        if route_frame.empty:
            vehicle_frames[vehicle_number] = task_frame.reset_index(drop=True)
        else:
            vehicle_frames[vehicle_number] = pd.concat([
                route_frame.iloc[:insert_at],
                task_frame,
                route_frame.iloc[insert_at:],
            ], ignore_index=True)

    output_frames = []
    for vehicle_number in (1, 2):
        route_frame = vehicle_frames[vehicle_number].copy()
        if not route_frame.empty:
            route_frame["방문순서"] = range(1, len(route_frame) + 1)
            output_frames.append(route_frame)

    if not output_frames:
        return visits.iloc[0:0].copy().reset_index(drop=True)
    return pd.concat(output_frames, ignore_index=True).sort_values(
        ["차량번호", "방문순서"]
    ).reset_index(drop=True)


# ==================================================
# 3. 카카오맵 HTML 생성
# ==================================================

def create_kakao_map_html(route_info, vehicle_visits, js_key):

    route_points = route_info["route_points"]

    if route_points.empty:
        raise ValueError("표시할 도로 경로 좌표가 없습니다.")

    # 도로 경로 좌표
    route_data = [{"lat": float(row["위도"]),
                   "lng": float(row["경도"])} 
                   for _, row in route_points.iterrows()]

    # 방문 정류소 정보
    stops = vehicle_visits.sort_values("방문순서")

    stop_data = []
    for _, row in stops.iterrows():
        quantity = pd.to_numeric(row.get("작업수량", 0), errors="coerce")
        suspect_count = pd.to_numeric(
            row.get("고장 의심 이용 건수(건)", 0), errors="coerce"
        )
        stop_data.append({
            "order": int(row["방문순서"]),
            "type": str(row["방문유형"]),
            "name": str(row["정류소명"]),
            "quantity": int(quantity) if pd.notna(quantity) else None,
            "extra": str(row.get("작업설명", "") or ""),
            "suspectCount": int(suspect_count) if pd.notna(suspect_count) else 0,
            "lat": float(row["위도"]),
            "lng": float(row["경도"]),
        })

    # JavaScript에 안전하게 전달
    route_json = json.dumps(route_data, ensure_ascii=False).replace("<", "\\u003c")

    stop_json = json.dumps(stop_data, ensure_ascii=False).replace("<", "\\u003c")

    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">

        <style>
            html, body {margin: 0;padding: 0;
            width: 100%;height: 100%;
            }

            #map-wrap {
                position: relative;
                width: 100%;
                height: 440px;
            }

            #map {
                width: 100%;
                height: 440px;
                border-radius: 8px;
            }

            #map-status {
                position: absolute;
                z-index: 10;
                top: 10px;
                left: 10px;
                padding: 6px 10px;
                background: white;
                border-radius: 5px;
                font-size: 12px;
                color: #555;
            }
        </style>

        <script src="https://dapi.kakao.com/v2/maps/sdk.js?appkey=__JS_KEY__&autoload=false" onerror="window.kakaoSdkLoadError = true;">
</script>
    </head>

    <body>

        <div id="map-wrap">
            <div id="map-status">카카오 지도 로딩 중...</div>
            <div id="map"></div>
        </div>

        <script>
            const routeData = __ROUTE_DATA__;
            const stopData = __STOP_DATA__;

            const statusBox = document.getElementById("map-status");

            if (window.kakaoSdkLoadError || !window.kakao || !window.kakao.maps) {
                statusBox.textContent = "SDK 로딩 실패 | 현재 도메인: " + window.location.origin + " | 호출 경로: " + document.referrer;

            } else {

                kakao.maps.load(function() {

                    try {

                        const center = new kakao.maps.LatLng(
                            routeData[0].lat,
                            routeData[0].lng
                        );

                        const map = new kakao.maps.Map(
                            document.getElementById("map"),
                            {
                                center: center,
                                level: 5
                            }
                        );

                        const bounds = new kakao.maps.LatLngBounds();

                        // 실제 도로 경로
                        const roadPath = routeData.map(function(point) {

                            const position = new kakao.maps.LatLng(
                                point.lat,
                                point.lng
                            );

                            bounds.extend(position);

                            return position;
                        });

                        if (roadPath.length > 1) {

                            new kakao.maps.Polyline({
                                map: map,
                                path: roadPath,
                                strokeWeight: 5,
                                strokeColor: "#2563EB",
                                strokeOpacity: 0.85,
                                strokeStyle: "solid"
                            });
                        }

                        // 정류소 마커 및 방문 순서
                        stopData.forEach(function(stop) {

                            const position = new kakao.maps.LatLng(
                                stop.lat,
                                stop.lng
                            );

                            bounds.extend(position);

                            const marker = new kakao.maps.Marker({
                                map: map,
                                position: position,
                                clickable: true,
                                title: stop.order + ". " + stop.name
                            });

                            // 방문 순서 번호 표시
                            const badge = document.createElement("div");

                            badge.textContent = String(stop.order);

                            const badgeColor = (stop.type.indexOf("점검") >= 0 || stop.suspectCount > 0)
                                ? "#2563EB"
                                : (stop.type === "수거" ? "#EA580C" : "#16A34A");

                            badge.style.cssText = `
                                width: 25px;
                                height: 25px;
                                border-radius: 50%;
                                background: ${badgeColor};
                                color: white;
                                border: 2px solid white;
                                display: flex;
                                align-items: center;
                                justify-content: center;
                                font-size: 12px;
                                font-weight: bold;
                                box-shadow: 0 1px 4px rgba(0,0,0,0.4);
                                cursor: pointer;
                            `;

                            // 마우스를 올리거나 클릭할 수 있도록 방문 번호 배지에도 제목을 지정한다.
                            badge.title = stop.order + ". " + stop.name;

                            const infoContent = document.createElement("div");
                            infoContent.style.cssText = `
                                padding: 10px 14px;
                                min-width: 150px;
                                font-size: 12px;
                                line-height: 1.7;
                            `;
                            let detailText = stop.order + ". " + stop.name + " / " + stop.type;
                            if (stop.quantity !== null && stop.type.indexOf("점검") < 0) {
                                detailText += " / " + stop.quantity + "대";
                            }
                            if (stop.extra) {
                                detailText += " / " + stop.extra;
                            }
                            if (stop.suspectCount > 0) {
                                detailText += " (고장 의심 이용 " + stop.suspectCount + "건)";
                            }
                            infoContent.textContent = detailText;

                            const infoWindow = new kakao.maps.InfoWindow({
                                content: infoContent,
                                removable: true
                            });

                            // 기본 마커: hover 시 표시, 클릭 시에도 동일한 정보창 표시.
                            kakao.maps.event.addListener(marker, "mouseover", function() {
                                infoWindow.open(map, marker);
                            });
                            kakao.maps.event.addListener(marker, "mouseout", function() {
                                infoWindow.close();
                            });
                            kakao.maps.event.addListener(marker, "click", function() {
                                infoWindow.open(map, marker);
                            });

                            // 방문 번호 배지가 마커 위를 덮으므로 배지에도 같은 이벤트를 연결한다.
                            badge.addEventListener("mouseenter", function() {
                                infoWindow.open(map, marker);
                            });
                            badge.addEventListener("mouseleave", function() {
                                infoWindow.close();
                            });
                            badge.addEventListener("click", function(event) {
                                event.stopPropagation();
                                infoWindow.open(map, marker);
                            });

                            new kakao.maps.CustomOverlay({
                                map: map,
                                position: position,
                                content: badge,
                                yAnchor: 1.5,
                                zIndex: 10
                            });
                        });

                        // 전체 도로 경로가 보이도록 지도 범위 설정
                        map.setBounds(bounds);

                        statusBox.style.display = "none";

                    } catch (error) {

                        statusBox.textContent =
                            "지도 표시 오류: " + error.message;
                    }
                });
            }
        </script>

    </body>
    </html>
    """

    html = html.replace("__JS_KEY__",escape(js_key, quote=True))

    html = html.replace("__ROUTE_DATA__", route_json)
    html = html.replace("__STOP_DATA__", stop_json)

    return html


# ==================================================
# 4. 재배치 계산 및 도로 경로 생성
# ==================================================

def build_vehicle_routes(visit_df):
    """차량별 방문 순서로 카카오모빌리티 도로 경로를 조회한다."""
    api_key = get_kakao_api_key()
    route_results = {}
    route_errors = {}

    for vehicle_number in [1, 2]:
        vehicle_visits = visit_df[
            visit_df["차량번호"] == vehicle_number
        ].copy()

        if vehicle_visits.empty:
            route_errors[vehicle_number] = "배정된 작업이 없습니다."
            continue

        if len(vehicle_visits) == 1:
            route_results[vehicle_number] = {
                "route_points": vehicle_visits[["위도", "경도"]].rename(
                    columns={"위도": "위도", "경도": "경도"}
                ).copy(),
                "distance_m": None,
                "duration_sec": None,
                "marker_only": True,
            }
            continue

        if not api_key:
            route_errors[vehicle_number] = (
                "카카오모빌리티 REST API 키가 설정되지 않았습니다. "
                ".streamlit/secrets.toml을 확인해 주세요."
            )
            continue

        try:
            route_results[vehicle_number] = get_vehicle_route(
                vehicle_visits,
                api_key=api_key,
                priority="RECOMMEND",
            )
        except Exception as error:
            route_errors[vehicle_number] = str(error)

    return route_results, route_errors


def build_default_vehicle_selections(assigned_plan_df):
    """자동 배정 결과를 바탕으로 정류소별 담당 차량을 한 곳에 지정한다."""
    selections = {
        1: {"supply_ids": [], "demand_ids": []},
        2: {"supply_ids": [], "demand_ids": []},
    }

    if assigned_plan_df.empty:
        return selections

    def assign_station_owners(station_column, selection_key):
        weights = (
            assigned_plan_df.groupby([station_column, "차량번호"])["이동수량"]
            .sum()
            .reset_index()
            .sort_values(
                [station_column, "이동수량", "차량번호"],
                ascending=[True, False, True],
            )
            .drop_duplicates(station_column)
        )
        for row in weights.itertuples(index=False):
            station_id = str(getattr(row, station_column))
            vehicle_number = int(row.차량번호)
            selections[vehicle_number][selection_key].append(station_id)

    assign_station_owners("공급정류소_ID", "supply_ids")
    assign_station_owners("수요정류소_ID", "demand_ids")
    return selections


def calculate_relocation_result(
    stage,
    target_date,
    target_hour,
    relocation_settings,
    result_key,
    revision,
    vehicle_selections=None,
    inspection_candidates=None,
):
    """재배치 작업에 고장 의심 정류소의 점검 방문을 자동 포함해 경로를 계산한다."""
    if vehicle_selections is None:
        plan_df, supply_df, demand_df = create_relocation_plan(
            stage=stage,
            target_date=target_date,
            target_hour=target_hour,
            relocation_settings=relocation_settings,
        )
        assigned_plan_df = assign_vehicles(plan_df, vehicle_count=2)
        vehicle_selections = build_default_vehicle_selections(assigned_plan_df)
    else:
        supply_owners = {}
        demand_owners = {}

        for vehicle_number in [1, 2]:
            selection = vehicle_selections.get(vehicle_number, {})
            for station_id in selection.get("supply_ids", []):
                station_id = str(station_id)
                if station_id in supply_owners:
                    raise ValueError(
                        f"공급 정류소 {station_id}가 차량 1·2에 중복 선택되었습니다. "
                        "한 정류소는 한 차량에만 지정해 주세요."
                    )
                supply_owners[station_id] = vehicle_number

            for station_id in selection.get("demand_ids", []):
                station_id = str(station_id)
                if station_id in demand_owners:
                    raise ValueError(
                        f"수요 정류소 {station_id}가 차량 1·2에 중복 선택되었습니다. "
                        "한 정류소는 한 차량에만 지정해 주세요."
                    )
                demand_owners[station_id] = vehicle_number

        selected_supply_ids = list(supply_owners)
        selected_demand_ids = list(demand_owners)
        plan_df, supply_df, demand_df = create_relocation_plan(
            stage=stage,
            target_date=target_date,
            target_hour=target_hour,
            relocation_settings=relocation_settings,
            selected_supply_ids=selected_supply_ids,
            selected_demand_ids=selected_demand_ids,
            vehicle_assignments={
                "supply": supply_owners,
                "demand": demand_owners,
            },
        )
        assigned_plan_df = assign_vehicles(plan_df, vehicle_count=2)

    base_visit_df = build_vehicle_visit_plan(assigned_plan_df)
    base_visit_df = attach_station_coordinates(base_visit_df)
    visit_df = add_inspection_visits_to_route(
        base_visit_df,
        inspection_candidates,
    )
    route_results, route_errors = build_vehicle_routes(visit_df)

    return {
        "key": result_key,
        "revision": revision,
        "plan": plan_df,
        "assigned_plan": assigned_plan_df,
        "visits": visit_df,
        "base_visits": base_visit_df,
        "inspection_station_count": (
            int(inspection_candidates["대여소_ID"].nunique())
            if inspection_candidates is not None and not inspection_candidates.empty else 0
        ),
        "supply": supply_df,
        "demand": demand_df,
        "routes": route_results,
        "route_errors": route_errors,
        "vehicle_selections": vehicle_selections,
    }


def build_station_selection_table(
    supply_df,
    demand_df,
    vehicle_selections,
    vehicle_number,
):
    """선택한 차량의 공급·수요 후보 목록을 표시용 표로 만든다."""
    selection = vehicle_selections.get(vehicle_number, {})
    selected_supply_ids = set(map(str, selection.get("supply_ids", [])))
    selected_demand_ids = set(map(str, selection.get("demand_ids", [])))

    supply_view = pd.DataFrame({
        "구분": "공급",
        "정류소명": supply_df["정류소명"].astype(str),
        "수량": supply_df["공급가능량"].astype(int),
        "선정": supply_df["공급정류소_ID"].astype(str).isin(selected_supply_ids),
    })
    supply_view.index = "공급::" + supply_df["공급정류소_ID"].astype(str)

    demand_view = pd.DataFrame({
        "구분": "수요",
        "정류소명": demand_df["정류소명"].astype(str),
        "수량": demand_df["필요수량"].astype(int),
        "선정": demand_df["수요정류소_ID"].astype(str).isin(selected_demand_ids),
    })
    demand_view.index = "수요::" + demand_df["수요정류소_ID"].astype(str)

    result = pd.concat([supply_view, demand_view])
    result.index.name = "station_key"
    return result


# ==================================================
# 5. 상단 조건 선택
# ==================================================

SEOUL_DISTRICTS = [
    "종로구", "중구", "용산구", "성동구", "광진구",
    "동대문구", "중랑구", "성북구", "강북구", "도봉구",
    "노원구", "은평구", "서대문구", "마포구", "양천구",
    "강서구", "구로구", "금천구", "영등포구", "동작구",
    "관악구", "서초구", "강남구", "송파구", "강동구",
]

# 이전 프로모션 실행 조건이 있으면 기본 선택값으로만 활용한다.
previous_execution = st.session_state.get("promotion_execution", {})
if not isinstance(previous_execution, dict):
    previous_execution = {}

try:
    default_stage = int(previous_execution.get("stage", 1))
except (TypeError, ValueError):
    default_stage = 1
if default_stage not in [1, 2, 3]:
    default_stage = 1

default_district = str(previous_execution.get("district", "강서구"))
if default_district not in SEOUL_DISTRICTS:
    default_district = "강서구"

header_cols = st.columns([1.45, 1.15, 0.85, 1.25, 0.85, 1.35], gap="small")

with header_cols[0]:
    render_page_title("재배치 계획")

with header_cols[1]:
    selected_district = st.selectbox(
        "행정구 선택",
        options=SEOUL_DISTRICTS,
        index=SEOUL_DISTRICTS.index(default_district),
        key="relocation_district",
    )

with header_cols[2]:
    stage = st.selectbox(
        "프로모션 단계",
        options=[1, 2, 3],
        index=[1, 2, 3].index(default_stage),
        format_func=lambda value: f"{value}단계",
        key="relocation_stage",
    )

try:
    available_dates = get_available_dates(stage)
except Exception as error:
    st.error(f"프로모션 날짜 정보를 불러오지 못했습니다: {error}")
    st.stop()

if not available_dates:
    st.warning("선택한 프로모션 단계에 사용할 수 있는 날짜 결과가 없습니다.")
    st.stop()

try:
    previous_date = pd.to_datetime(
        previous_execution.get("date"), errors="coerce"
    ).strftime("%Y-%m-%d")
except (TypeError, ValueError, AttributeError):
    previous_date = None

with header_cols[3]:
    selected_date = st.selectbox(
        "기준 날짜",
        options=available_dates,
        index=available_dates.index(previous_date) if previous_date in available_dates else 0,
        key="relocation_date",
    )

try:
    available_hours = get_available_hours(stage, selected_date)
except Exception as error:
    st.error(f"프로모션 시간 정보를 불러오지 못했습니다: {error}")
    st.stop()

if not available_hours:
    st.warning("선택한 날짜에 사용할 수 있는 시간대 결과가 없습니다.")
    st.stop()

try:
    default_hour = int(previous_execution.get("hour", available_hours[0]))
except (TypeError, ValueError):
    default_hour = available_hours[0]
if default_hour not in available_hours:
    default_hour = available_hours[0]

with header_cols[4]:
    selected_hour = st.selectbox(
        "기준 시간",
        options=available_hours,
        index=available_hours.index(default_hour),
        format_func=lambda value: f"{value:02d}시",
        key="relocation_hour",
    )

with header_cols[5]:
    st.write("")
    calculate_clicked = st.button(
        "재배치 작업 계산",
        type="primary",
        use_container_width=True,
        key="calculate_relocation",
    )

render_operations_assistant("relocation")

if selected_district != "강서구":
    st.info(f"{selected_district} 데이터는 현재 준비 중입니다.")
    st.stop()

selected_date_label = pd.to_datetime(selected_date).strftime("%Y-%m-%d")

try:
    promotion_result_path = get_result_path(stage, selected_date)
    promotion_signature = get_promotion_result_signature(promotion_result_path)
except (OSError, ValueError, FileNotFoundError) as error:
    st.error(f"선택한 프로모션 결과를 확인할 수 없습니다: {error}")
    st.stop()

relocation_settings = load_relocation_settings()
settings_signature = (
    relocation_settings["max_relocation_distance_km"],
    relocation_settings["min_transfer_quantity"],
    relocation_settings["max_work_bundles"],
)

# 선택 날짜·시간의 고장 의심 정류소를 먼저 읽어 초기 경로 계산부터 자동 반영한다.
try:
    (
        inspection_candidates,
        suspect_data_available,
        suspect_source_name,
        suspect_signature,
    ) = load_inspection_candidates(
        selected_date,
        selected_hour,
        aggregation_basis="도착시간",
    )
except Exception as error:
    inspection_candidates = pd.DataFrame(columns=[
        "대여소_ID", "대여소명", "고장 의심 이용 건수(건)"
    ])
    suspect_data_available = False
    suspect_source_name = f"점검 방문 데이터 조회 실패: {type(error).__name__}: {error}"
    suspect_signature = ()

current_key = (
    selected_district,
    int(stage),
    selected_date,
    int(selected_hour),
    settings_signature,
    promotion_signature,
    suspect_signature,
)

key_token = hashlib.sha1(
    repr(current_key).encode("utf-8")
).hexdigest()[:10]

if calculate_clicked:
    existing_result = st.session_state.get("relocation_result")
    if isinstance(existing_result, dict) and existing_result.get("key") == current_key:
        revision = int(existing_result.get("revision", 0)) + 1
    else:
        revision = 0

    try:
        st.session_state["relocation_result"] = calculate_relocation_result(
            stage=stage,
            target_date=selected_date,
            target_hour=selected_hour,
            relocation_settings=relocation_settings,
            result_key=current_key,
            revision=revision,
            inspection_candidates=inspection_candidates,
        )
    except Exception as error:
        st.session_state.pop("relocation_result", None)
        st.error(f"재배치 계산 중 오류가 발생했습니다: {error}")
        st.stop()


# ==================================================
# 6. 계산 결과 및 재배치 현황
# ==================================================

result = st.session_state.get("relocation_result")
if not result or result.get("key") != current_key:
    st.info("상단 조건을 선택한 뒤 '재배치 작업 계산'을 눌러 결과를 확인하세요.")
    st.stop()

plan_df = result["plan"]
assigned_plan_df = result["assigned_plan"]
visit_df = result["visits"].copy()

# 이전 세션에 저장된 재배치 결과에는 점검 방문 신규 컬럼이 없을 수 있다.
# 화면 표와 Excel 작업지시서에서 KeyError가 발생하지 않도록 기본 컬럼을 보장한다.
if "작업설명" not in visit_df.columns:
    visit_df["작업설명"] = ""
else:
    visit_df["작업설명"] = visit_df["작업설명"].fillna("")

if "고장 의심 이용 건수(건)" not in visit_df.columns:
    visit_df["고장 의심 이용 건수(건)"] = 0
else:
    visit_df["고장 의심 이용 건수(건)"] = pd.to_numeric(
        visit_df["고장 의심 이용 건수(건)"], errors="coerce"
    ).fillna(0).astype(int)

# 보정한 방문 결과를 세션 결과에도 반영해 화면·지도·Excel이 같은 데이터를 사용하게 한다.
result["visits"] = visit_df

supply_df = result["supply"]
demand_df = result["demand"]

selection_draft_key = f"relocation_selection_draft_{key_token}_{result['revision']}"
if selection_draft_key not in st.session_state:
    st.session_state[selection_draft_key] = {
        vehicle: {
            "supply_ids": list(result.get("vehicle_selections", {}).get(vehicle, {}).get("supply_ids", [])),
            "demand_ids": list(result.get("vehicle_selections", {}).get(vehicle, {}).get("demand_ids", [])),
        }
        for vehicle in [1, 2]
    }

total_supply = int(supply_df["공급가능량"].sum())
total_demand = int(demand_df["필요수량"].sum())
total_moved = int(supply_df["배정수량"].sum())
remaining_demand = int(demand_df["잔여부족량"].sum())


def show_kpi_card(label, value, accent_color):
    st.markdown(f"""
    <div style="
        background: var(--secondary-background-color);
        color: var(--text-color);
        border: 1px solid rgba(128,128,128,0.25);
        border-top: 4px solid {accent_color};
        border-radius: 8px;
        padding: 14px 12px;
        min-height: 105px;
        box-sizing: border-box;
    ">
        <div style="font-size: 0.85rem; opacity: 0.7; margin-bottom: 12px;">
            {label}
        </div>
        <div style="font-size: 1.5rem; font-weight: 700; color: {accent_color};">
            {value}
        </div>
    </div>
    """, unsafe_allow_html=True)


st.divider()
st.subheader("재배치 현황")
st.caption(
    f"프로모션 {stage}단계 결과를 기준으로 계산했습니다."
)

kpi1, kpi2, kpi3, kpi4 = st.columns(4)
with kpi1:
    show_kpi_card("공급 가능량", f"{total_supply:,}대", "#447DB8")
with kpi2:
    show_kpi_card("공급 필요량", f"{total_demand:,}대", "#269B83")
with kpi3:
    show_kpi_card("총 이동 예정량", f"{total_moved:,}대", "#E3A23B")
with kpi4:
    show_kpi_card("매칭 후 잔여 부족량", f"{remaining_demand:,}대", "#8B6BCB")


# ==================================================
# 7. 지도와 작업 배치를 좌우로 배치
# ==================================================

st.divider()
st.subheader("차량별 경로 및 작업 배치")
st.caption(
    "차량을 선택하면 지도, 작업 배치, 정류소 선정표가 모두 해당 차량 기준으로 전환됩니다."
)

vehicle_number = st.radio(
    "차량 선택",
    options=[1, 2],
    format_func=lambda value: f"차량 {value}",
    horizontal=True,
    key=f"relocation_vehicle_selector_{key_token}",
)

vehicle_jobs = assigned_plan_df[
    assigned_plan_df["차량번호"] == vehicle_number
].copy()
vehicle_visits = visit_df[
    visit_df["차량번호"] == vehicle_number
].copy()
route_info = result["routes"].get(vehicle_number)
route_error = result["route_errors"].get(vehicle_number)

map_col, work_col = st.columns([1, 1.05], gap="large")

with map_col:
    st.markdown(f"### 차량 {vehicle_number} 도로 경로")

    job_count = len(vehicle_jobs)
    move_quantity = (
        int(vehicle_jobs["이동수량"].sum())
        if not vehicle_jobs.empty else 0
    )
    stop_count = len(vehicle_visits)
    inspection_stop_count = (
        int(pd.to_numeric(
            vehicle_visits.get("고장 의심 이용 건수(건)", pd.Series(0, index=vehicle_visits.index)),
            errors="coerce",
        ).fillna(0).gt(0).sum())
        if not vehicle_visits.empty else 0
    )

    if route_info is not None and not route_info.get("marker_only", False):
        metric1, metric2 = st.columns(2)
        metric1.metric(
            "예상 도로 거리",
            f"{route_info['distance_m'] / 1000:,.1f}km",
        )
        metric2.metric(
            "예상 주행시간",
            f"{round(route_info['duration_sec'] / 60):,}분",
        )
    elif route_info is not None:
        metric1, metric2 = st.columns(2)
        metric1.metric("예상 도로 거리", "-")
        metric2.metric("예상 주행시간", "-")
    else:
        metric1, metric2 = st.columns(2)
        metric1.metric("예상 도로 거리", "-")
        metric2.metric("예상 주행시간", "-")

    st.caption(
        f"작업 묶음 {job_count:,}건 · 이동 예정량 {move_quantity:,}대 · "
        f"방문 정류소 {stop_count:,}곳 · 점검 방문 {inspection_stop_count:,}곳"
    )

    js_key = get_kakao_map_js_key()
    if vehicle_visits.empty:
        st.info("선택된 재배치 작업이 없어 표시할 경로가 없습니다.")
    elif route_error:
        st.warning(f"도로 경로를 불러오지 못했습니다: {route_error}")
    elif route_info is None:
        st.info("조회된 도로 경로가 없습니다.")
    elif not js_key:
        st.warning(
            "카카오맵 JavaScript 키가 없습니다. "
            ".streamlit/secrets.toml을 확인해 주세요."
        )
    else:
        try:
            map_html = create_kakao_map_html(route_info, vehicle_visits, js_key)
            components.html(map_html, height=460, scrolling=False)
        except Exception as error:
            st.error(f"카카오맵 생성 오류: {error}")

    if st.button(
        "작업 배치 반영 및 경로 갱신",
        type="primary",
        use_container_width=True,
        key="apply_relocation_work_allocation",
    ):
        vehicle_selections = st.session_state[selection_draft_key]
        try:
            updated_result = calculate_relocation_result(
                stage=stage,
                target_date=selected_date,
                target_hour=selected_hour,
                relocation_settings=relocation_settings,
                result_key=current_key,
                revision=int(result.get("revision", 0)) + 1,
                vehicle_selections=vehicle_selections,
                inspection_candidates=inspection_candidates,
            )
            st.session_state["relocation_result"] = updated_result
            st.rerun()
        except Exception as error:
            st.error(f"작업 배치와 점검 방문 경로를 갱신하지 못했습니다: {error}")

with work_col:
    inspection_bell_html = ""

    if suspect_data_available:
        inspection_station_count = (
            inspection_candidates["대여소_ID"].nunique()
            if inspection_candidates is not None and not inspection_candidates.empty
            else 0
        )

        inspection_notice = (
            f"고장 의심 정류소 {inspection_station_count:,}곳을 "
            "추가 이동거리가 가장 적은 차량 경로에 자동으로 포함했습니다."
        )

        inspection_bell_html = (
            f'<span class="inspection-bell-tooltip" role="img" tabindex="0" '
            f'aria-label="{escape(inspection_notice, quote=True)}" '
            f'data-tooltip="{escape(inspection_notice, quote=True)}">🔔</span>'
        )

    st.markdown(
        '<div class="work-adjustment-title-row">'
        '<h3>작업 배치 조정</h3>'
        f'{inspection_bell_html}'
        '</div>',
        unsafe_allow_html=True,
    )

    work_tab, selection_tab = st.tabs(["작업 배치", "정류소 선정"])
    with work_tab:
        st.markdown("**수거 및 배치 작업**")
        if vehicle_jobs.empty:
            st.info("해당 차량에 배정된 작업이 없습니다.")
        else:
            job_view = vehicle_jobs[[
                "차량작업순번",
                "공급정류소명",
                "수요정류소명",
                "이동수량",
                "직선거리_m",
            ]].rename(columns={
                "차량작업순번": "작업 순번",
                "공급정류소명": "수거 정류소",
                "수요정류소명": "배치 정류소",
                "이동수량": "이동 대수",
                "직선거리_m": "직선거리(m)",
            })
            st.dataframe(
                job_view,
                use_container_width=True,
                hide_index=True,
                height=250,
            )

        st.markdown("**방문 순서 및 작업 정보**")
        if vehicle_visits.empty:
            st.info("표시할 방문 정보가 없습니다.")
        else:
            visit_view = vehicle_visits[[
                "방문순서",
                "방문유형",
                "정류소명",
                "작업수량",
                "고장 의심 이용 건수(건)",
            ]].copy()
            visit_view.loc[visit_view["방문유형"].eq("점검 방문"), "작업수량"] = None
            visit_view = visit_view.rename(columns={
                "방문순서": "방문 순서",
                "방문유형": "작업 유형",
                "정류소명": "정류소",
                "작업수량": "작업 대수",
                "고장 의심 이용 건수(건)": "고장 의심(건)",
            })
            st.dataframe(
                visit_view,
                use_container_width=True,
                hide_index=True,
                height=240,
            )

    with selection_tab:
        draft_selections = st.session_state[selection_draft_key]
        selection_state = draft_selections.get(vehicle_number, {})
        st.markdown(f"**차량 {vehicle_number} 담당 정류소**")
        st.caption(
            "선택한 차량이 담당할 공급·수요 정류소를 지정합니다. "
            "한 정류소는 한 차량에만 지정할 수 있으며, 공급과 수요 정류소가 같은 차량에 지정되어야 매칭됩니다."
        )
        st.caption(
            f"현재 선택: 공급 {len(selection_state.get('supply_ids', []))}곳 · "
            f"수요 {len(selection_state.get('demand_ids', []))}곳"
        )
        station_selection_df = build_station_selection_table(
            supply_df,
            demand_df,
            draft_selections,
            vehicle_number,
        )
        station_editor_df = st.data_editor(
            station_selection_df,
            key=f"relocation_station_editor_{key_token}_{result['revision']}_{vehicle_number}",
            use_container_width=True,
            hide_index=True,
            num_rows="fixed",
            height=390,
            disabled=["구분", "정류소명", "수량"],
            column_order=["구분", "정류소명", "수량", "선정"],
            column_config={
                "구분": st.column_config.TextColumn("유형", width="small"),
                "정류소명": st.column_config.TextColumn("정류소명", width="large"),
                "수량": st.column_config.NumberColumn("가능/필요량(대)", format="%d", width="small"),
                "선정": st.column_config.CheckboxColumn(
                    "선정",
                    help="이 차량이 담당할 공급 또는 수요 정류소를 선택합니다.",
                ),
            },
        )

        # 차량 전환 후에도 이전 차량의 편집 상태가 유지되도록 임시 선택값을 갱신한다.
        edited_selection = station_editor_df.copy()
        edited_selection["선정"] = edited_selection["선정"].fillna(False).astype(bool)
        station_ids = pd.Series(
            edited_selection.index.astype(str),
            index=edited_selection.index,
        ).str.split("::", n=1).str[1]
        draft_selections[vehicle_number] = {
            "supply_ids": station_ids.loc[
                (edited_selection["구분"] == "공급") & edited_selection["선정"]
            ].tolist(),
            "demand_ids": station_ids.loc[
                (edited_selection["구분"] == "수요") & edited_selection["선정"]
            ].tolist(),
        }
        st.session_state[selection_draft_key] = draft_selections


# ==================================================
# 9. 배차 계획 확정 및 전달
# ==================================================

st.divider()
st.subheader("배차 계획 확정 및 전달")
st.caption("최종 재배치 계획을 확정하고 운송기사에게 작업 일정을 전달합니다.")

st.markdown("""
<style>
.st-key-dispatch-actions div[data-testid="stButton"] > button {
    width: 100%;
    height: 48px;
    min-height: 48px;
    white-space: nowrap;
    display: flex;
    align-items: center;
    justify-content: center;
}
</style>
""", unsafe_allow_html=True)

excel_data = create_work_order_excel(assigned_plan_df, visit_df)
with st.container(key="dispatch-actions"):
    col1, col2 = st.columns(2, gap="medium")

    with col1:
        st.download_button(
            label="🖨 차량별 작업지시서 Excel 다운로드",
            data=excel_data,
            file_name=(
                f"vehicle_work_orders_stage{stage}_"
                f"{selected_date}_{selected_hour:02d}.xlsx"
            ),
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )

    with col2:
        st.button(
            "📨 기사에게 일정 전달",
            use_container_width=True,
            disabled=True,
        )
