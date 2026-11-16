import json
import math
import os
import re
from datetime import datetime
from html import escape
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st
import streamlit.components.v1 as components

from utils.operations_assistant import render_operations_assistant
from utils.page_header import render_page_title
from utils.promotion_simulator import (
    DEFAULT_PROMOTION_SETTINGS,
    PROMOTION_SETTINGS_PATH,
    load_current_promotion_settings,
)
from utils.relocation_planner import (
    get_available_hours,
    get_promotion_result_signature,
    load_station_master,
)


# ==========================================
# 1. 파일 경로 및 날짜 목록
# ==========================================

BASE_DIR = Path(__file__).resolve().parents[1]
PROMOTION_RESULTS_DIR = BASE_DIR / "outputs" / "promotion_senario_results"


def get_available_promotion_dates(stage: int) -> list[str]:
    """선택한 단계의 결과 파일이 존재하는 날짜를 최신순으로 반환한다."""
    if not PROMOTION_RESULTS_DIR.exists():
        return []

    result_name = f"promotion_result_stage{stage}.csv"
    dates = []

    for folder in PROMOTION_RESULTS_DIR.iterdir():
        if not folder.is_dir() or not re.fullmatch(r"\d{8}", folder.name):
            continue

        comparison_path = folder / "promotion_participation_cost_comparison.csv"
        result_path = folder / result_name

        if comparison_path.is_file() and result_path.is_file():
            dates.append(folder.name)

    return sorted(dates, reverse=True)


# ==========================================
# 2. 행정구·시나리오·기준일·기준 시간 선택
# ==========================================

SEOUL_DISTRICTS = [
    "종로구", "중구", "용산구", "성동구", "광진구",
    "동대문구", "중랑구", "성북구", "강북구", "도봉구",
    "노원구", "은평구", "서대문구", "마포구", "양천구",
    "강서구", "구로구", "금천구", "영등포구", "동작구",
    "관악구", "서초구", "강남구", "송파구", "강동구",
]

# 제목과 조회 조건을 한 행에 배치한다.
title_col, district_col, scenario_col, date_col, hour_col = st.columns(
    [2.1, 1.05, 1.2, 1.25, 0.8],
    gap="small",
)

with title_col:
    render_page_title("프로모션 시뮬레이션")

with district_col:
    selected_district = st.selectbox(
        "행정구 선택",
        SEOUL_DISTRICTS,
        index=SEOUL_DISTRICTS.index("강서구"),
        key="promotion_district_select",
    )

with scenario_col:
    selected_stage = st.selectbox(
        "운영 시나리오",
        [1, 2, 3],
        format_func=lambda value: f"프로모션 {value}단계",
        key="promotion_stage_select",
    )

available_dates = get_available_promotion_dates(selected_stage)

if not available_dates:
    st.error(
        f"프로모션 {selected_stage}단계 결과를 찾을 수 없습니다. "
        "먼저 utils.promotion_simulator를 실행해 주세요."
    )
    st.stop()

if st.session_state.get("promotion_date_select") not in available_dates:
    st.session_state["promotion_date_select"] = available_dates[0]

with date_col:
    selected_date = st.selectbox(
        "분석 기준일",
        available_dates,
        format_func=lambda value: pd.to_datetime(value, format="%Y%m%d").strftime("%Y-%m-%d"),
        key="promotion_date_select",
    )

try:
    available_hours = get_available_hours(selected_stage, selected_date)
except Exception as error:
    st.error(f"선택한 날짜의 시간 정보를 불러오지 못했습니다: {error}")
    st.stop()

if not available_hours:
    st.error("선택한 날짜의 프로모션 결과에 시간대 정보가 없습니다.")
    st.stop()

hour_selection_key = (selected_date, int(selected_stage))
if st.session_state.get("_promotion_hour_selection_key") != hour_selection_key:
    st.session_state["promotion_hour_select"] = (
        9 if 9 in available_hours else available_hours[0]
    )
    st.session_state["_promotion_hour_selection_key"] = hour_selection_key
elif st.session_state.get("promotion_hour_select") not in available_hours:
    st.session_state["promotion_hour_select"] = (
        9 if 9 in available_hours else available_hours[0]
    )

with hour_col:
    selected_hour = st.selectbox(
        "기준 시간",
        available_hours,
        format_func=lambda value: f"{value:02d}:00",
        key="promotion_hour_select",
    )

# AI 운영 도우미에는 현재 선택값과 실제 적용 설정을 전달한다.
try:
    current_promotion_settings = load_current_promotion_settings()
    promotion_settings_source = (
        "config/promotion_settings.json 저장값"
        if PROMOTION_SETTINGS_PATH.is_file()
        else "설정 파일이 없어 코드 기본값을 현재 실행값으로 사용"
    )
    promotion_assistant_context = {
        "행정구": selected_district,
        "선택 프로모션 단계": selected_stage,
        "분석 기준일": selected_date,
        "기준 시간": selected_hour,
        "현재 적용 프로모션 설정": current_promotion_settings,
        "설정 출처": promotion_settings_source,
        "프로모션 코드 기본값": DEFAULT_PROMOTION_SETTINGS,
        "이동 1대당 기대 비용 지표": (
            "예상 마일리지 비용을 기대 이동량으로 나눈 값이다. 프로모션 단계별 비용 효율을 "
            "보조적으로 비교하는 지표이며, 실제 이용자 1회당 비용이나 실제 운영 비용을 의미하지 않는다."
        ),
        "거리 구간별 기대 이동량 분포": (
            "각 단계의 전체 이동 계획에서 기대 이동량을 실제 적용된 거리별 추가 보상 구간으로 나눈다. "
            "비율은 각 단계의 기대 이동량 합계 대비 구간별 비중이다. 거리는 도로 주행거리가 아니라 "
            "공급·수요 정류소 사이의 직선거리이며, 거리 기준과 추가 보상 포인트는 선택한 날짜의 "
            "시뮬레이션 실행 당시 적용 설정을 따른다."
        ),
        "연결 1건당 기대 이동량": (
            "전체 기대 이동량을 계획 연결 건수로 나눈 평균이다. 여기서 연결 1건은 특정 시간대의 "
            "공급 정류소와 수요 정류소 간 매칭 행 1건을 뜻한다. 동일한 정류소 조합이 다른 시간대에 "
            "반복되면 각각 별도 건으로 집계하며, 실제 운행 횟수나 실제 이동 건수를 의미하지 않는다."
        ),
        "프로모션 KPI 계산 기준": (
            "기대 이동량은 계획 이동량에 참여율을 곱한 평균 기대값이며 소수점이 될 수 있다. "
            "예상 마일리지 비용도 기대 이동량을 기준으로 계산한다. 반면 모의 부족량 감소율과 "
            "모의 초과량 감소율은 참여율에 따라 확률 추출한 정수 모의 이동량을 재고에 반영한 "
            "각 연결별 확률 추출을 한 차례 수행한 시뮬레이션 결과를 프로모션 미적용 기준과 비교한 값이다. 두 종류 지표는 계산 기준이 다르다."
        ),
    }
except Exception as error:
    promotion_assistant_context = {
        "행정구": selected_district,
        "선택 프로모션 단계": selected_stage,
        "분석 기준일": selected_date,
        "기준 시간": selected_hour,
        "설정 조회 상태": f"현재 프로모션 설정을 읽지 못함: {error}",
        "이동 1대당 기대 비용 지표": (
            "예상 마일리지 비용을 기대 이동량으로 나눈 값이다. 프로모션 단계별 비용 효율을 "
            "보조적으로 비교하는 지표이며, 실제 이용자 1회당 비용이나 실제 운영 비용을 의미하지 않는다."
        ),
        "거리 구간별 기대 이동량 분포": (
            "각 단계의 기대 이동량을 시뮬레이션의 거리별 추가 보상 구간으로 나눈 비율이다. "
            "직선거리 기준이며 도로 주행거리가 아니다."
        ),
        "연결 1건당 기대 이동량": (
            "전체 기대 이동량을 시간대별 공급·수요 정류소 계획 연결 건수로 나눈 평균이다. "
            "실제 운행 횟수나 실제 이동 건수와는 다르다."
        ),
        "프로모션 KPI 계산 기준": (
            "기대 이동량은 계획 이동량×참여율의 평균 기대값이고, 예상 마일리지 비용도 기대 이동량 기준이다. "
            "모의 부족량·초과량 감소율은 확률 추출한 정수 모의 이동량을 반영한 단일 시뮬레이션 결과를 "
            "미적용 기준과 비교한 값이다."
        ),
    }

render_operations_assistant("promotion", context=promotion_assistant_context)

if selected_district != "강서구":
    st.info(f"{selected_district} 데이터는 현재 준비 중입니다. 현재는 강서구만 분석할 수 있습니다.")
    st.stop()

selected_result_dir = PROMOTION_RESULTS_DIR / selected_date
PROMOTION_PATH = selected_result_dir / "promotion_participation_cost_comparison.csv"
PROMOTION_RESULT_PATH = selected_result_dir / f"promotion_result_stage{selected_stage}.csv"


@st.cache_data
def load_promotion_data(
    file_path: str,
    modified_time_ns: int,
    file_size: int,
) -> pd.DataFrame:
    """선택한 날짜의 프로모션 비교 결과를 불러온다."""
    return pd.read_csv(file_path, encoding="utf-8-sig")


@st.cache_data
def load_transfer_data(
    file_path: str,
    modified_time_ns: int,
    file_size: int,
) -> pd.DataFrame:
    """선택한 날짜와 단계의 정류소 간 이동 계획을 불러온다."""
    return pd.read_csv(file_path, encoding="utf-8-sig")


@st.cache_data
def load_transfer_summary_data(
    file_path: str,
    modified_time_ns: int,
    file_size: int,
) -> pd.DataFrame:
    """단계별 그래프 계산에 필요한 컬럼만 읽어 온다."""
    return pd.read_csv(
        file_path,
        usecols=["직선거리_km", "기대이동량"],
        encoding="utf-8-sig",
    )


def get_kakao_map_js_key() -> str:
    """환경 변수 또는 Streamlit secrets에서 카카오 지도 JavaScript 키를 읽는다."""
    js_key = os.getenv("KAKAO_MAP_JAVASCRIPT_KEY", "").strip()
    if js_key:
        return js_key

    try:
        return str(st.secrets["KAKAO_MAP_JAVASCRIPT_KEY"]).strip()
    except Exception:
        return ""


def normalize_station_ids(values: pd.Series) -> pd.Series:
    """정류소 ID를 문자열로 통일하고 CSV의 소수점 표기를 제거한다."""
    return values.astype("string").str.strip().str.replace(r"\.0$", "", regex=True)


def build_promotion_transfer_map_data(transfer_data: pd.DataFrame) -> tuple[list[dict], list[dict]]:
    """이동 계획의 공급·수요 정류소를 마스터 좌표에 연결해 지도 데이터로 변환한다."""
    required = ["공급정류소", "수요정류소", "기대이동량", "직선거리_km"]
    missing = [column for column in required if column not in transfer_data.columns]
    if missing:
        raise ValueError(f"지도 생성에 필요한 이동 계획 컬럼이 없습니다: {missing}")

    station_data = load_station_master().copy()
    station_data["대여소_ID"] = normalize_station_ids(station_data["대여소_ID"])
    station_data = station_data.set_index("대여소_ID")

    plan = transfer_data.copy()
    plan["공급정류소"] = normalize_station_ids(plan["공급정류소"])
    plan["수요정류소"] = normalize_station_ids(plan["수요정류소"])
    plan["기대이동량"] = pd.to_numeric(plan["기대이동량"], errors="coerce").fillna(0)
    plan["직선거리_km"] = pd.to_numeric(plan["직선거리_km"], errors="coerce")

    if "가상이동량" in plan.columns:
        plan["가상이동량"] = pd.to_numeric(plan["가상이동량"], errors="coerce").fillna(0)
    else:
        plan["가상이동량"] = 0

    station_columns = {
        "대여소명": "명",
        "위도": "위도",
        "경도": "경도",
    }
    for role, id_column in (("공급", "공급정류소"), ("수요", "수요정류소")):
        role_station_data = station_data.rename(
            columns={column: f"{role}_{suffix}" for column, suffix in station_columns.items()}
        )
        plan = plan.join(role_station_data, on=id_column)

    coordinate_columns = ["공급_위도", "공급_경도", "수요_위도", "수요_경도"]
    for column in coordinate_columns:
        plan[column] = pd.to_numeric(plan[column], errors="coerce")

    valid_plan = plan.dropna(subset=coordinate_columns).copy()
    if valid_plan.empty:
        raise ValueError("선택 시간의 이동 계획에 지도에 표시할 수 있는 정류소 좌표가 없습니다.")
    if len(valid_plan) != len(plan):
        missing_count = len(plan) - len(valid_plan)
        raise ValueError(
            f"정류소 마스터에서 좌표를 찾지 못한 이동 계획이 {missing_count}건 있습니다. "
            "일부 연결만 표시하지 않도록 지도를 생략합니다."
        )

    markers_by_id: dict[str, dict] = {}

    def add_marker(row: pd.Series, role: str) -> None:
        station_id = str(row[f"{role}정류소"])
        marker = markers_by_id.setdefault(station_id, {
            "id": station_id,
            "name": str(row[f"{role}_명"]),
            "lat": float(row[f"{role}_위도"]),
            "lng": float(row[f"{role}_경도"]),
            "roles": set(),
            "expected_supply": 0.0,
            "expected_demand": 0.0,
            "simulated_supply": 0.0,
            "simulated_demand": 0.0,
        })
        marker["roles"].add(role)
        marker[f"expected_{'supply' if role == '공급' else 'demand'}"] += float(row["기대이동량"])
        marker[f"simulated_{'supply' if role == '공급' else 'demand'}"] += float(row["가상이동량"])

    pairs = []
    for _, row in valid_plan.iterrows():
        add_marker(row, "공급")
        add_marker(row, "수요")
        pairs.append({
            "source_id": str(row["공급정류소"]),
            "target_id": str(row["수요정류소"]),
            "source_name": str(row["공급_명"]),
            "target_name": str(row["수요_명"]),
            "expected_moves": float(row["기대이동량"]),
            "distance_km": float(row["직선거리_km"]) if pd.notna(row["직선거리_km"]) else None,
            "source_lat": float(row["공급_위도"]),
            "source_lng": float(row["공급_경도"]),
            "target_lat": float(row["수요_위도"]),
            "target_lng": float(row["수요_경도"]),
        })

    markers = []
    for marker in markers_by_id.values():
        roles = marker.pop("roles")
        # 공급·수요 겸용 색상은 사용하지 않는다. 중복 역할이 발생하면 공급 마커를 우선한다.
        role_label = "공급" if "공급" in roles else "수요"
        expected_total = marker["expected_supply"] + marker["expected_demand"]
        marker.update({
            "role_label": role_label,
            "expected_total": expected_total,
        })
        markers.append(marker)

    return markers, pairs


def create_promotion_kakao_map_html(transfer_data: pd.DataFrame, js_key: str) -> str:
    """공급·수요 정류소와 정류소 간 기대 이동 관계를 표시하는 카카오 지도를 만든다."""
    markers, pairs = build_promotion_transfer_map_data(transfer_data)
    marker_json = json.dumps(markers, ensure_ascii=False).replace("<", "\\u003c")
    pair_json = json.dumps(pairs, ensure_ascii=False).replace("<", "\\u003c")

    html = r"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <style>
            html, body { margin: 0; padding: 0; width: 100%; height: 100%; }
            #map-wrap { position: relative; width: 100%; height: 500px; }
            #map { width: 100%; height: 500px; border-radius: 8px; }
            #map-status {
                display: none; position: absolute; z-index: 10; top: 10px; left: 10px;
                padding: 6px 10px; background: white; border-radius: 5px;
                font-size: 12px; color: #B42318;
            }
            #map-legend {
                position: absolute; z-index: 10; top: 10px; right: 10px;
                display: grid; grid-template-columns: auto auto; gap: 6px 10px;
                padding: 8px 10px; background: rgba(255, 255, 255, 0.94);
                border: 1px solid rgba(100, 116, 139, 0.22); border-radius: 7px;
                box-shadow: 0 1px 4px rgba(15, 23, 42, 0.12);
                font-size: 11px; line-height: 1.2; color: #334155;
            }
            .legend-item { display: inline-flex; align-items: center; gap: 5px; white-space: nowrap; }
            .legend-dot { width: 9px; height: 9px; border-radius: 50%; display: inline-block; flex: 0 0 9px; }
            .legend-line { width: 17px; border-top: 2px dashed #64748B; display: inline-block; flex: 0 0 17px; }
            .station-info {
                padding: 8px 11px; min-width: 150px;
                font-size: 12px; line-height: 1.55; color: #222;
                white-space: nowrap;
            }
        </style>
        <script src="https://dapi.kakao.com/v2/maps/sdk.js?appkey=__JS_KEY__&autoload=false" onerror="window.kakaoSdkLoadError = true;"></script>
    </head>
    <body>
        <div id="map-wrap">
            <div id="map-status" role="status" aria-live="polite"></div>
            <div id="map-legend" aria-label="지도 범례">
                <span class="legend-item"><span class="legend-dot" style="background:#2563EB;"></span>공급</span>
                <span class="legend-item"><span class="legend-dot" style="background:#DC5A4A;"></span>수요</span>
                <span class="legend-item"><span class="legend-line"></span>매칭</span>
            </div>
            <div id="map"></div>
        </div>
        <script>
            const markerData = __MARKER_DATA__;
            const pairData = __PAIR_DATA__;
            const statusBox = document.getElementById("map-status");

            function showMapError(message) {
                statusBox.textContent = message;
                statusBox.style.display = "block";
            }

            if (window.kakaoSdkLoadError || !window.kakao || !window.kakao.maps) {
                showMapError("카카오 지도 SDK를 불러오지 못했습니다.");
            } else {
                kakao.maps.load(function() {
                    try {
                        const first = markerData[0];
                        const map = new kakao.maps.Map(document.getElementById("map"), {
                            center: new kakao.maps.LatLng(first.lat, first.lng),
                            level: 6
                        });
                        const bounds = new kakao.maps.LatLngBounds();
                        const markerById = {};

                        markerData.forEach(function(item) {
                            const position = new kakao.maps.LatLng(item.lat, item.lng);
                            bounds.extend(position);

                            const markerColor = item.role_label === "공급" ? "#2563EB" : "#DC5A4A";
                            const markerSvg = '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20"><circle cx="10" cy="10" r="7" fill="' + markerColor + '" stroke="#FFFFFF" stroke-width="2"/></svg>';
                            const markerImage = new kakao.maps.MarkerImage(
                                "data:image/svg+xml;charset=utf-8," + encodeURIComponent(markerSvg),
                                new kakao.maps.Size(20, 20),
                                { offset: new kakao.maps.Point(10, 10) }
                            );
                            const marker = new kakao.maps.Marker({
                                map: map, position: position, clickable: true,
                                image: markerImage,
                                title: item.name + " " + item.role_label
                            });
                            markerById[item.id] = marker;

                            const infoContent = document.createElement("div");
                            infoContent.className = "station-info";
                            const infoTitle = document.createElement("strong");
                            infoTitle.textContent = item.name + " " + item.role_label;
                            const infoDetail = document.createElement("div");
                            infoDetail.textContent = "기대 이동량: " + item.expected_total.toFixed(1) + "대";
                            infoContent.appendChild(infoTitle);
                            infoContent.appendChild(infoDetail);

                            const infoWindow = new kakao.maps.InfoWindow({ content: infoContent });
                            const openInfo = function() { infoWindow.open(map, marker); };
                            const closeInfo = function() { infoWindow.close(); };
                            kakao.maps.event.addListener(marker, "mouseover", openInfo);
                            kakao.maps.event.addListener(marker, "mouseout", closeInfo);
                            kakao.maps.event.addListener(marker, "click", openInfo);
                        });

                        pairData.forEach(function(pair) {
                            const sourcePosition = new kakao.maps.LatLng(pair.source_lat, pair.source_lng);
                            const targetPosition = new kakao.maps.LatLng(pair.target_lat, pair.target_lng);
                            const line = new kakao.maps.Polyline({
                                map: map, path: [sourcePosition, targetPosition],
                                strokeWeight: 3, strokeColor: "#64748B",
                                strokeOpacity: 0.65, strokeStyle: "shortdash"
                            });
                            const pairInfoContent = document.createElement("div");
                            pairInfoContent.className = "station-info";
                            const pairInfoTitle = document.createElement("strong");
                            pairInfoTitle.textContent = pair.source_name + " → " + pair.target_name;
                            const pairInfoDetail = document.createElement("div");
                            pairInfoDetail.textContent = "기대 이동량 " + pair.expected_moves.toFixed(1) + "대 · 직선거리 " +
                                (pair.distance_km === null ? "정보 없음" : pair.distance_km.toFixed(2) + "km");
                            pairInfoContent.appendChild(pairInfoTitle);
                            pairInfoContent.appendChild(pairInfoDetail);
                            const lineInfo = new kakao.maps.InfoWindow({ content: pairInfoContent });
                            kakao.maps.event.addListener(line, "mouseover", function() {
                                lineInfo.open(map, markerById[pair.source_id]);
                            });
                            kakao.maps.event.addListener(line, "mouseout", function() {
                                lineInfo.close();
                            });
                            kakao.maps.event.addListener(line, "click", function() {
                                lineInfo.open(map, markerById[pair.source_id]);
                            });
                        });

                        map.setBounds(bounds);
                        statusBox.style.display = "none";
                    } catch (error) {
                        showMapError("지도 표시 오류: " + error.message);
                    }
                });
            }
        </script>
    </body>
    </html>
    """

    html = html.replace("__JS_KEY__", escape(js_key, quote=True))
    html = html.replace("__MARKER_DATA__", marker_json)
    html = html.replace("__PAIR_DATA__", pair_json)
    return html


@st.cache_data
def load_promotion_settings(
    file_path: str,
    modified_time_ns: int,
    file_size: int,
) -> dict:
    """선택한 날짜의 프로모션 실행 설정 이력을 불러온다."""
    with open(file_path, "r", encoding="utf-8") as file:
        return json.load(file)


def format_stage_rule(rule: dict) -> str:
    """단계별 공급·수요 우선구간을 화면 표시용 문자열로 변환한다."""
    return (
        f"공급 1~{int(rule['max_source_band'])}구간 / "
        f"수요 {int(rule['min_target_band'])}~10구간"
    )


def build_promotion_settings_display(
    default_settings: dict,
    applied_settings: dict,
) -> pd.DataFrame:
    """기본값과 실제 적용값을 비교하는 화면용 표를 만든다."""
    rows = []

    def add_row(label: str, default_value: str, applied_value: str) -> None:
        rows.append({
            "설정 항목": label,
            "기본값": default_value,
            "적용값": applied_value,
        })

    for stage in (1, 2, 3):
        stage_key = str(stage)
        default_rate = default_settings["participation_rates"][stage_key]
        applied_rate = applied_settings["participation_rates"][stage_key]
        add_row(
            f"{stage}단계 참여율",
            f"{float(default_rate) * 100:g}%",
            f"{float(applied_rate) * 100:g}%",
        )

        default_rule = default_settings["stage_rules"][stage_key]
        applied_rule = applied_settings["stage_rules"][stage_key]
        add_row(
            f"{stage}단계 대상 우선구간",
            format_stage_rule(default_rule),
            format_stage_rule(applied_rule),
        )

    for key, label in (
        ("min_distance_km", "최소 이동 거리"),
        ("max_distance_km", "최대 이동 거리"),
    ):
        add_row(
            label,
            f"{float(default_settings[key]):g}km",
            f"{float(applied_settings[key]):g}km",
        )

    def format_bonus(settings: dict, first_tier: bool) -> str:
        min_distance = float(settings["min_distance_km"])
        threshold = float(settings["bonus_distance_threshold_km"])
        max_distance = float(settings["max_distance_km"])

        if first_tier:
            distance_range = f"{min_distance:g}km 이상 ~ {threshold:g}km 미만"
            points = int(settings["bonus_mileage_1_points"])
        else:
            distance_range = f"{threshold:g}km 이상 ~ {max_distance:g}km 이하"
            points = int(settings["bonus_mileage_2_points"])

        return f"{distance_range}: {points:,}P"

    add_row(
        "기본 마일리지",
        f"{int(default_settings['base_mileage_points']):,}P",
        f"{int(applied_settings['base_mileage_points']):,}P",
    )
    add_row(
        "거리별 추가 보상 1",
        format_bonus(default_settings, True),
        format_bonus(applied_settings, True),
    )
    add_row(
        "거리별 추가 보상 2",
        format_bonus(default_settings, False),
        format_bonus(applied_settings, False),
    )
    add_row(
        "마일리지 원화 환산",
        f"{float(default_settings['point_value_krw']):g}원/P",
        f"{float(applied_settings['point_value_krw']):g}원/P",
    )

    return pd.DataFrame(rows)


if not PROMOTION_PATH.exists():
    st.error(f"선택한 날짜의 프로모션 비교 파일을 찾을 수 없습니다: {PROMOTION_PATH}")
    st.stop()

try:
    promotion_stat = PROMOTION_PATH.stat()
    promotion = load_promotion_data(
        str(PROMOTION_PATH),
        promotion_stat.st_mtime_ns,
        promotion_stat.st_size,
    )
except Exception as e:
    st.error(f"프로모션 비교 데이터 불러오기 오류: {e}")
    st.stop()



# ==========================================
# 3. 데이터 전처리 및 컬럼 확인
# ==========================================

required_columns = [
    "시나리오", "기대이동량", "기대마일리지_원",
    "부족량_감소율_기준대비(%)", "초과량_감소율_기준대비(%)"
]
missing_columns = [col for col in required_columns if col not in promotion.columns]

if missing_columns:
    st.error(f"필요한 컬럼이 없습니다: {missing_columns}")
    st.stop()

numeric_columns = [
    "기대이동량", "기대마일리지_원",
    "부족량_감소율_기준대비(%)", "초과량_감소율_기준대비(%)"
]
for col in numeric_columns:
    promotion[col] = pd.to_numeric(promotion[col], errors="coerce")

promotion["시나리오"] = promotion["시나리오"].astype(str).str.strip()


# ==========================================
# 4. 공통 시나리오 및 색상 설정
# ==========================================

STAGE_ORDER = ["프로모션 1단계", "프로모션 2단계", "프로모션 3단계"]
STAGE_COLORS = {
    "프로모션 1단계": "#447DB8",
    "프로모션 2단계": "#E3A23B",
    "프로모션 3단계": "#269B83"
}
EFFECT_COLORS = {
    "모의 부족량 감소율": "#E45756",
    "모의 초과량 감소율": "#2878A5"
}


# ==========================================
# 5. 선택한 프로모션 시나리오 확인
# ==========================================

stage_name = f"프로모션 {selected_stage}단계"
stage_data = promotion[promotion["시나리오"] == stage_name]

if stage_data.empty:
    st.warning("선택한 단계의 비교 결과가 없습니다.")
    st.stop()

stage_row = stage_data.iloc[0]

st.caption("하단 KPI와 단계별 효과 비교는 선택 날짜의 하루 전체 누적 기준입니다. 기준 시간은 시간대별 이동 계획 조회와 직접 재배치 연계에 적용됩니다.")

# 선택한 날짜의 실행 당시 설정 이력 표시
SETTINGS_PATH = selected_result_dir / "promotion_settings.json"
chart_applied_settings = DEFAULT_PROMOTION_SETTINGS

with st.expander("프로모션 시뮬레이션 설정 확인", expanded=False):
    if SETTINGS_PATH.is_file():
        try:
            settings_stat = SETTINGS_PATH.stat()
            settings_history = load_promotion_settings(
                str(SETTINGS_PATH),
                settings_stat.st_mtime_ns,
                settings_stat.st_size,
            )
            default_settings = settings_history["default_settings"]
            applied_settings = settings_history["applied_settings"]
            chart_applied_settings = applied_settings
            settings_display = build_promotion_settings_display(
                default_settings,
                applied_settings,
            )
            st.caption("선택한 날짜의 시뮬레이션 실행 당시 설정입니다.")
            st.dataframe(
                settings_display,
                hide_index=True,
                use_container_width=True,
            )
        except Exception as e:
            st.warning(f"프로모션 설정 이력을 불러오지 못했습니다: {e}")
    else:
        st.info(
            "이 날짜의 결과에는 실행 당시 설정 이력이 저장되어 있지 않습니다. "
            "수정된 promotion_simulator.py로 시뮬레이션을 다시 실행하면 설정 이력이 저장됩니다."
        )

st.divider()


# ==========================================
# 6. 선택 단계의 핵심 지표
# ==========================================

st.subheader("선택 시나리오 주요 지표")

# 직접 재배치 페이지의 '재배치 현황' KPI 카드와 동일한 디자인을 사용한다.
def show_promotion_kpi_card(label: str, value: str, accent_color: str) -> None:
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
            {escape(label)}
        </div>
        <div style="font-size: 1.5rem; font-weight: 700; color: {accent_color};">
            {escape(value)}
        </div>
    </div>
    """, unsafe_allow_html=True)


kpi_columns = st.columns(4)
metric_cards = [
    (
        "기대 이동량",
        f"{stage_row['기대이동량']:,.1f}대" if pd.notna(stage_row["기대이동량"]) else "자료 없음",
        "#447DB8",
    ),
    (
        "예상 마일리지 비용",
        f"{stage_row['기대마일리지_원']:,.0f}원" if pd.notna(stage_row["기대마일리지_원"]) else "자료 없음",
        "#269B83",
    ),
    (
        "모의 부족량 감소율",
        f"{stage_row['부족량_감소율_기준대비(%)']:.2f}%" if pd.notna(stage_row["부족량_감소율_기준대비(%)"]) else "자료 없음",
        "#E3A23B",
    ),
    (
        "모의 초과량 감소율",
        f"{stage_row['초과량_감소율_기준대비(%)']:.2f}%" if pd.notna(stage_row["초과량_감소율_기준대비(%)"]) else "자료 없음",
        "#8B6BCB",
    ),
]

for column, (label, value_text, accent_color) in zip(kpi_columns, metric_cards):
    with column:
        show_promotion_kpi_card(label, value_text, accent_color)

st.divider()


# ==========================================
# 7. 단계별 시나리오 비교
# ==========================================

st.subheader("단계별 시나리오 비교")
comparison = promotion[required_columns].drop_duplicates(subset=["시나리오"], keep="last").copy()

# 전체 비교표에는 미적용 기준을 유지하되, 그래프에서는 제거
comparison_chart = comparison[comparison["시나리오"] != "프로모션 미적용"].copy()
comparison_chart = comparison_chart[comparison_chart["시나리오"].isin(STAGE_ORDER)].copy()
comparison_chart["시나리오"] = pd.Categorical(comparison_chart["시나리오"], categories=STAGE_ORDER, ordered=True)
comparison_chart = comparison_chart.sort_values("시나리오")

stage_chart_col, effect_chart_col = st.columns(2, gap="large")

with stage_chart_col:
    st.markdown("**단계별 기대 이동량 · 예상 마일리지 비용**")

    stage_labels = comparison_chart["시나리오"].astype(str).tolist()
    expected_moves = pd.to_numeric(comparison_chart["기대이동량"], errors="coerce").tolist()
    expected_costs = pd.to_numeric(comparison_chart["기대마일리지_원"], errors="coerce").tolist()
    expected_moves_text = [f"{value:,.1f}대" if pd.notna(value) else "" for value in expected_moves]
    expected_costs_text = [f"{value:,.0f}원" if pd.notna(value) else "" for value in expected_costs]

    valid_moves = [value for value in expected_moves if pd.notna(value)]
    valid_costs = [value for value in expected_costs if pd.notna(value)]
    max_moves = max(valid_moves, default=0)
    max_cost = max(valid_costs, default=0)
    move_axis_max = max(max_moves * 1.22, 1)
    cost_tick_step = max(1, math.ceil(max_cost / 4))
    cost_axis_max = max(cost_tick_step, math.ceil(max_cost / cost_tick_step) * cost_tick_step)

    # 하나의 그래프에서 기대 이동량은 막대, 비용은 보조 Y축 선으로 표시한다.
    fig_stage = make_subplots(specs=[[{"secondary_y": True}]])
    fig_stage.add_trace(
        go.Bar(
            x=stage_labels,
            y=expected_moves,
            name="기대 이동량",
            marker_color=[STAGE_COLORS[stage] for stage in stage_labels],
            text=expected_moves_text,
            textposition="outside",
            textfont=dict(size=12),
            cliponaxis=False,
            hovertemplate="기대 이동량: %{y:,.1f}대<extra></extra>",
        ),
        secondary_y=False,
    )
    cost_line_color = "#DC5A4A"
    fig_stage.add_trace(
        go.Scatter(
            x=stage_labels,
            y=expected_costs,
            name="예상 마일리지 비용",
            mode="lines+markers",
            line=dict(color=cost_line_color, width=3),
            marker=dict(size=10, color=cost_line_color, line=dict(color="#FFFFFF", width=1.5)),
            cliponaxis=False,
            hovertemplate="예상 마일리지 비용: %{y:,.0f}원<extra></extra>",
        ),
        secondary_y=True,
    )

    # 비용 수치는 투명 배경에 흰색 글씨로 표시해 막대 수치와 구분한다.
    for stage_label, cost_value in zip(stage_labels, expected_costs):
        if pd.notna(cost_value):
            fig_stage.add_annotation(
                x=stage_label,
                y=cost_value,
                xref="x",
                yref="y2",
                text=f"{cost_value:,.0f}원",
                showarrow=False,
                xshift=0,
                yshift=-22,
                bgcolor="rgba(0,0,0,0)",
                bordercolor="rgba(0,0,0,0)",
                borderpad=0,
                font=dict(size=11, color="#FFFFFF"),
            )
    fig_stage.update_layout(
        height=420,
        margin=dict(l=20, r=24, t=48, b=12),
        showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        bargap=0.42,
        hovermode="x unified",
    )
    fig_stage.update_xaxes(
        title_text="",
        categoryorder="array",
        categoryarray=STAGE_ORDER,
    )
    fig_stage.update_yaxes(
        title_text="기대 이동량(대)",
        rangemode="tozero",
        range=[0, move_axis_max],
        tickformat=",.1f",
        secondary_y=False,
    )
    fig_stage.update_yaxes(
        title_text="예상 마일리지 비용(원)",
        tickmode="linear",
        tick0=0,
        dtick=cost_tick_step,
        tickformat=",.0f",
        range=[0, max(cost_axis_max * 1.3, 1)],
        secondary_y=True,
    )
    st.plotly_chart(fig_stage, use_container_width=True)

with effect_chart_col:
    st.markdown("**단계별 운영 효과 비교**")
    effect_data = comparison_chart.melt(
        id_vars=["시나리오"],
        value_vars=["부족량_감소율_기준대비(%)", "초과량_감소율_기준대비(%)"],
        var_name="효과 구분", value_name="감소율(%)"
    )
    effect_data["효과 구분"] = effect_data["효과 구분"].map({
        "부족량_감소율_기준대비(%)": "모의 부족량 감소율",
        "초과량_감소율_기준대비(%)": "모의 초과량 감소율"
    })

    fig_effect = px.bar(
        effect_data, x="시나리오", y="감소율(%)", color="효과 구분", barmode="group",
        color_discrete_map=EFFECT_COLORS,
        category_orders={"시나리오": STAGE_ORDER, "효과 구분": ["모의 부족량 감소율", "모의 초과량 감소율"]},
        labels={"감소율(%)": "모의 기준 대비 감소율(%)"},
        text_auto=".1f"
    )
    fig_effect.update_layout(
        height=380,
        margin=dict(l=10, r=10, t=40, b=12),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0, title=""),
    )
    fig_effect.update_traces(
        textposition="outside",
        hovertemplate="모의 기준 대비 감소율: %{y:.1f}%<extra></extra>",
    )
    fig_effect.update_xaxes(title_text="")
    st.plotly_chart(fig_effect, use_container_width=True)


# ==========================================
# 9. 거리 구간별 기대 이동량 및 연결당 기대 이동량
# ==========================================

# 선택 날짜에 저장된 단계별 이동 계획을 이용해 1~3단계를 같은 기준으로 재집계한다.
distance_min = float(chart_applied_settings["min_distance_km"])
distance_threshold = float(chart_applied_settings["bonus_distance_threshold_km"])
distance_max = float(chart_applied_settings["max_distance_km"])
bonus_1_points = int(chart_applied_settings["bonus_mileage_1_points"])
bonus_2_points = int(chart_applied_settings["bonus_mileage_2_points"])

distance_band_labels = [
    f"{distance_min:g}~{distance_threshold:g}km (추가 {bonus_1_points:,}P)",
    f"{distance_threshold:g}~{distance_max:g}km (추가 {bonus_2_points:,}P)",
]
distance_rows = []
connection_rows = []
stage_file_errors = []

for stage in (1, 2, 3):
    stage_name = f"프로모션 {stage}단계"
    stage_transfer_path = selected_result_dir / f"promotion_transfer_stage{stage}.csv"

    if not stage_transfer_path.is_file():
        stage_file_errors.append(f"{stage}단계 이동 계획 파일 없음")
        continue

    try:
        transfer_stat = stage_transfer_path.stat()
        stage_plan = load_transfer_summary_data(
            str(stage_transfer_path),
            transfer_stat.st_mtime_ns,
            transfer_stat.st_size,
        )
        required_plan_columns = ["직선거리_km", "기대이동량"]
        missing_plan_columns = [column for column in required_plan_columns if column not in stage_plan.columns]
        if missing_plan_columns:
            raise ValueError(f"필요한 컬럼이 없습니다: {missing_plan_columns}")

        stage_plan["직선거리_km"] = pd.to_numeric(stage_plan["직선거리_km"], errors="coerce")
        stage_plan["기대이동량"] = pd.to_numeric(stage_plan["기대이동량"], errors="coerce").fillna(0)
        valid_plan = stage_plan.dropna(subset=["직선거리_km"]).copy()

        first_band = valid_plan[
            valid_plan["직선거리_km"].ge(distance_min)
            & valid_plan["직선거리_km"].lt(distance_threshold)
        ]
        second_band = valid_plan[
            valid_plan["직선거리_km"].ge(distance_threshold)
            & valid_plan["직선거리_km"].le(distance_max)
        ]

        first_expected = float(first_band["기대이동량"].sum())
        second_expected = float(second_band["기대이동량"].sum())
        total_expected = first_expected + second_expected

        for band_label, expected_moves in zip(
            distance_band_labels,
            (first_expected, second_expected),
        ):
            distance_rows.append({
                "시나리오": stage_name,
                "거리 구간": band_label,
                "기대이동량": expected_moves,
                "구성비(%)": expected_moves / total_expected * 100 if total_expected > 0 else 0,
            })

        planned_connections = len(stage_plan)
        connection_rows.append({
            "시나리오": stage_name,
            "계획연결건수": planned_connections,
            "기대이동량합계": float(stage_plan["기대이동량"].sum()),
            "연결당_기대이동량": (
                float(stage_plan["기대이동량"].sum()) / planned_connections
                if planned_connections > 0 else float("nan")
            ),
        })
    except Exception as error:
        stage_file_errors.append(f"{stage}단계 이동 계획 처리 실패: {error}")

if stage_file_errors:
    st.warning("일부 시나리오 데이터를 불러오지 못했습니다: " + " / ".join(stage_file_errors))

movement_col, connection_col = st.columns(2, gap="large")

with movement_col:
    st.markdown("**거리 구간별 기대 이동량 분포**")
    if not distance_rows:
        st.info("거리 구간별 기대 이동량을 계산할 이동 계획 데이터가 없습니다.")
    else:
        distance_chart_data = pd.DataFrame(distance_rows)
        distance_chart_data["시나리오"] = pd.Categorical(
            distance_chart_data["시나리오"], categories=STAGE_ORDER, ordered=True
        )
        distance_chart_data["거리 구간"] = pd.Categorical(
            distance_chart_data["거리 구간"], categories=distance_band_labels, ordered=True
        )
        distance_chart_data = distance_chart_data.sort_values(["시나리오", "거리 구간"])

        fig_distance = px.bar(
            distance_chart_data,
            x="시나리오",
            y="구성비(%)",
            color="거리 구간",
            barmode="stack",
            custom_data=["기대이동량"],
            color_discrete_sequence=["#447DB8", "#E3A23B"],
            category_orders={"시나리오": STAGE_ORDER, "거리 구간": distance_band_labels},
            labels={"구성비(%)": "기대 이동량 구성비(%)"},
        )
        fig_distance.update_traces(
            texttemplate="%{y:.1f}%",
            textposition="inside",
            hovertemplate=(
                "%{fullData.name}"
                "<br>구성비: %{y:.1f}%"
                "<br>기대 이동량: %{customdata[0]:,.1f}대<extra></extra>"
            ),
        )
        fig_distance.update_layout(
            height=380,
            margin=dict(l=12, r=18, t=24, b=12),
            barmode="stack",
            yaxis=dict(range=[0, 100], ticksuffix="%"),
            legend_title_text="거리·추가 보상 구간",
        )
        fig_distance.update_xaxes(title_text="")
        st.plotly_chart(fig_distance, use_container_width=True)

with connection_col:
    st.markdown("**연결 1건당 기대 이동량**")
    connection_chart_data = pd.DataFrame(connection_rows)
    if connection_chart_data.empty or connection_chart_data["연결당_기대이동량"].notna().sum() == 0:
        st.info("연결당 기대 이동량을 계산할 데이터가 없습니다.")
    else:
        connection_chart_data = connection_chart_data.dropna(subset=["연결당_기대이동량"]).copy()
        connection_chart_data["시나리오"] = pd.Categorical(
            connection_chart_data["시나리오"], categories=STAGE_ORDER, ordered=True
        )
        connection_chart_data = connection_chart_data.sort_values("시나리오")

        fig_connection = px.bar(
            connection_chart_data,
            x="시나리오",
            y="연결당_기대이동량",
            color="시나리오",
            color_discrete_map=STAGE_COLORS,
            category_orders={"시나리오": STAGE_ORDER},
            custom_data=["기대이동량합계", "계획연결건수"],
            text_auto=".2f",
            labels={"연결당_기대이동량": "연결 1건당 기대 이동량(대)"},
        )
        fig_connection.update_traces(
            textposition="outside",
            hovertemplate=(
                "연결당 기대 이동량: %{y:.2f}대"
                "<br>전체 기대 이동량: %{customdata[0]:,.1f}대"
                "<br>계획 연결 건수: %{customdata[1]:,}건<extra></extra>"
            ),
        )
        fig_connection.update_layout(
            height=380,
            margin=dict(l=12, r=18, t=24, b=12),
            showlegend=False,
            yaxis=dict(rangemode="tozero"),
        )
        fig_connection.update_xaxes(title_text="")
        st.plotly_chart(fig_connection, use_container_width=True)

st.divider()


# ==========================================
# 10. 정류소 간 이동 계획 및 지도
# ==========================================

transfer_path = selected_result_dir / f"promotion_transfer_stage{selected_stage}.csv"

if transfer_path.exists():
    try:
        transfer_stat = transfer_path.stat()
        transfer_data = load_transfer_data(
            str(transfer_path),
            transfer_stat.st_mtime_ns,
            transfer_stat.st_size,
        )

        if "기준시각" not in transfer_data.columns:
            raise ValueError("이동 계획 파일에 '기준시각' 컬럼이 없어 시간대별 조회를 할 수 없습니다.")

        transfer_data["기준시각"] = pd.to_datetime(
            transfer_data["기준시각"], errors="coerce"
        )
        selected_day = pd.to_datetime(selected_date, format="%Y%m%d").date()
        selected_transfer = transfer_data.loc[
            transfer_data["기준시각"].dt.date.eq(selected_day)
            & transfer_data["기준시각"].dt.hour.eq(int(selected_hour))
        ].copy()

        st.markdown(
            f"""
            <div style="display:flex; align-items:baseline; gap:12px; margin:0 0 12px 0;">
                <span style="font-size:1.5rem; font-weight:600;">{selected_hour:02d}시 정류소 간 이동 계획</span>
                <span style="font-size:0.95rem; color:var(--text-color); opacity:0.7;">{len(selected_transfer):,}건</span>
            </div>
            """,
            unsafe_allow_html=True,
        )

        if selected_transfer.empty:
            st.info(f"{selected_hour:02d}시에 실행할 정류소 간 이동 계획이 없습니다.")
        else:
            map_col, plan_col = st.columns([1.0, 1.35], gap="large")

            with map_col:
                st.markdown("**공급·수요 정류소 이동 관계 지도**")
                js_key = get_kakao_map_js_key()
                if not js_key:
                    st.warning("카카오맵 JavaScript 키가 없습니다. .streamlit/secrets.toml의 KAKAO_MAP_JAVASCRIPT_KEY를 확인해 주세요.")
                else:
                    try:
                        map_html = create_promotion_kakao_map_html(selected_transfer, js_key)
                        components.html(map_html, height=520, scrolling=False)
                    except Exception as error:
                        st.warning(f"카카오 지도 데이터를 구성하지 못했습니다: {error}")
            with plan_col:
                st.markdown("**시간대별 이동 계획 요약**")
                st.caption("기대 이동량은 참여율을 반영한 평균 기대치입니다. 가상 이동량은 해당 시뮬레이션에서 추출된 정수 결과입니다.")

                display_data = selected_transfer.copy()
                display_data["기준시각"] = display_data["기준시각"].dt.strftime("%Y-%m-%d %H:%M")
                if "기대이동량" in display_data.columns:
                    display_data = display_data.sort_values("기대이동량", ascending=False, na_position="last")
                if "직선거리_km" in display_data.columns:
                    display_data["직선거리_km"] = pd.to_numeric(display_data["직선거리_km"], errors="coerce").round(2)
                if "기대이동량" in display_data.columns:
                    display_data["기대이동량"] = pd.to_numeric(display_data["기대이동량"], errors="coerce").round(1)
                compact_columns = [
                    "기준시각", "공급정류소", "수요정류소", "직선거리_km",
                    "계획이동량", "기대이동량", "가상이동량", "기대마일리지_원",
                ]
                compact_columns = [column for column in compact_columns if column in display_data.columns]
                compact_display = display_data[compact_columns].rename(columns={
                    "기준시각": "기준 시각",
                    "공급정류소": "공급 정류소",
                    "수요정류소": "수요 정류소",
                    "직선거리_km": "직선거리(km)",
                    "계획이동량": "계획 이동량(대)",
                    "기대이동량": "기대 이동량(대)",
                    "가상이동량": "가상 이동량(대)",
                    "기대마일리지_원": "예상 마일리지 비용(원)",
                })
                st.dataframe(
                    compact_display,
                    hide_index=True,
                    use_container_width=True,
                    height=460,
                )

            full_display = selected_transfer.copy()
            full_display["기준시각"] = full_display["기준시각"].dt.strftime("%Y-%m-%d %H:%M")
            with st.expander("전체 이동 계획 계산 상세 내역", expanded=False):
                st.dataframe(
                    full_display,
                    hide_index=True,
                    use_container_width=True,
                    height=400,
                )
                st.download_button(
                    "전체 이동 계획 CSV 다운로드",
                    data=selected_transfer.to_csv(index=False).encode("utf-8-sig"),
                    file_name=f"promotion_transfer_{selected_date}_{selected_hour:02d}_stage{selected_stage}.csv",
                    mime="text/csv",
                )
    except Exception as e:
        st.error(f"이동 계획 데이터 처리 오류: {e}")
else:
    st.subheader("정류소 간 이동 계획")
    st.warning(
        f"선택한 날짜({selected_date})의 {selected_stage}단계 이동 계획 파일을 찾을 수 없습니다."
    )


# ==========================================
# 11. 프로모션 실행
# ==========================================

st.divider()

promotion_result_path = selected_result_dir / f"promotion_result_stage{selected_stage}.csv"

# 제목은 왼쪽, 현재 적용된 시나리오는 같은 줄의 가장 오른쪽에 표시한다.
execution_title_col, execution_status_col = st.columns([1.15, 4.85], vertical_alignment="center")
with execution_title_col:
    st.subheader("프로모션 실행")
with execution_status_col:
    execution_status_placeholder = st.empty()

simulation_col, relocation_col = st.columns(2, gap="small")
with simulation_col:
    run_promotion = st.button(
        "선택한 프로모션 실행",
        type="primary",
        use_container_width=True,
        key="run_promotion_simulation",
    )


def format_confirmation_metric(value, suffix: str, decimal_places: int = 1) -> str:
    """프로모션 실행 확인창에 표시할 숫자를 포맷한다."""
    if pd.isna(value):
        return "자료 없음"
    return f"{float(value):,.{decimal_places}f}{suffix}"


@st.dialog("프로모션 실행 확인", width="large")
def show_promotion_execution_confirmation() -> None:
    """선택한 프로모션 조건과 주요 지표를 검토한 후 적용한다."""
    participation_rate = stage_row.get("참여율(%)")
    if pd.isna(participation_rate):
        try:
            participation_rate = (
                float(chart_applied_settings["participation_rates"][str(selected_stage)]) * 100
            )
        except (KeyError, TypeError, ValueError):
            participation_rate = float("nan")

    try:
        display_date = pd.to_datetime(selected_date, format="%Y%m%d").strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        display_date = str(selected_date)

    confirmation_data = pd.DataFrame([
        {"확인 항목": "기준일", "선택 내용": display_date},
        {"확인 항목": "프로모션 단계", "선택 내용": f"{selected_stage}단계"},
        {"확인 항목": "기준 시간", "선택 내용": f"{int(selected_hour):02d}:00"},
        {"확인 항목": "참여율", "선택 내용": format_confirmation_metric(participation_rate, "%", 1)},
        {"확인 항목": "기대 이동량", "선택 내용": format_confirmation_metric(stage_row.get("기대이동량"), "대", 1)},
        {"확인 항목": "예상 마일리지 비용", "선택 내용": format_confirmation_metric(stage_row.get("기대마일리지_원"), "원", 0)},
        {"확인 항목": "모의 부족량 감소율", "선택 내용": format_confirmation_metric(stage_row.get("부족량_감소율_기준대비(%)"), "%", 2)},
        {"확인 항목": "모의 초과량 감소율", "선택 내용": format_confirmation_metric(stage_row.get("초과량_감소율_기준대비(%)"), "%", 2)},
    ])

    st.dataframe(
        confirmation_data,
        hide_index=True,
        use_container_width=True,
        height=330,
    )
    st.caption(
        "핵심 지표는 선택 날짜의 하루 전체 기준이며, 기준 시간은 직접 재배치 연계에 사용됩니다."
    )
    st.info(
        "확인 후 선택한 사전 시뮬레이션 결과를 현재 운영 시나리오로 적용합니다. "
        "이 MVP는 실제 이용자 대상 캠페인 발송이나 마일리지 지급을 수행하지 않습니다."
    )

    cancel_col, confirm_col = st.columns(2)
    with cancel_col:
        if st.button("취소", use_container_width=True, key="cancel_promotion_execution"):
            st.rerun()
    with confirm_col:
        if st.button(
            "선택한 시나리오 적용",
            type="primary",
            use_container_width=True,
            key="confirm_promotion_execution",
        ):
            try:
                if not promotion_result_path.is_file():
                    raise FileNotFoundError(
                        f"선택한 단계의 결과 파일이 없습니다: {promotion_result_path.name}"
                    )

                st.session_state["promotion_execution"] = {
                    "district": selected_district,
                    "date": selected_date,
                    "hour": int(selected_hour),
                    "stage": int(selected_stage),
                    "executed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "result_signature": get_promotion_result_signature(promotion_result_path),
                }
                # 새 프로모션 시나리오를 적용하면 이전 직접 재배치·경로 결과를 폐기한다.
                st.session_state.pop("relocation_result", None)
                st.session_state.pop("route_result", None)
                st.rerun()
            except Exception as error:
                st.error(f"프로모션 적용에 실패했습니다: {error}")


if run_promotion:
    if not promotion_result_path.is_file():
        st.error(f"선택한 단계의 결과 파일이 없습니다: {promotion_result_path.name}")
    else:
        show_promotion_execution_confirmation()

# 실행 직후의 세션 상태를 다시 읽어 시나리오 표시와 재배치 버튼 활성 여부를 결정한다.
execution = st.session_state.get("promotion_execution")
execution_is_current = False
execution_date = ""
execution_stage = ""
execution_hour = ""
execution_district = ""

if isinstance(execution, dict):
    try:
        execution_date = str(execution["date"])
        execution_stage = int(execution["stage"])
        execution_hour = int(execution["hour"])
        execution_district = str(execution.get("district", "강서구"))
        execution_path = PROMOTION_RESULTS_DIR / execution_date / f"promotion_result_stage{execution_stage}.csv"
        current_signature = get_promotion_result_signature(execution_path)
        execution_hours = get_available_hours(execution_stage, execution_date)
        execution_is_current = (
            current_signature == execution.get("result_signature")
            and execution_hour in execution_hours
            and execution_district == "강서구"
        )
    except (KeyError, TypeError, ValueError, OSError, FileNotFoundError):
        execution_is_current = False

if execution_is_current:
    execution_status_placeholder.markdown(
        "<div style='width:100%; padding-top:0.35rem; text-align:right; font-size:0.92rem;'>"
        f"<strong>현재 적용된 시나리오:</strong> 프로모션 {execution_stage}단계"
        "</div>",
        unsafe_allow_html=True,
    )
elif isinstance(execution, dict):
    execution_status_placeholder.markdown(
        "<div style='width:100%; padding-top:0.35rem; text-align:right; font-size:0.92rem;'>"
        "이전 적용 결과 변경 · 재실행 필요"
        "</div>",
        unsafe_allow_html=True,
    )
    st.warning(
        "이전에 적용한 프로모션 결과가 변경되었거나 유효하지 않습니다. "
        "현재 조건으로 프로모션 시뮬레이션을 다시 실행해 주세요."
    )
else:
    execution_status_placeholder.markdown(
        "<div style='width:100%; padding-top:0.35rem; text-align:right; font-size:0.92rem;'>"
        "현재 적용된 시나리오: 없음"
        "</div>",
        unsafe_allow_html=True,
    )

with relocation_col:
        if st.button(
            "적용된 시나리오로 직접 재배치 진행",
            use_container_width=True,
            disabled=not execution_is_current,
            key="go_to_relocation_from_promotion",
        ):
            st.switch_page("pages/relocation.py")


