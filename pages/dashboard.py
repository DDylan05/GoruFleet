
import os

import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from pathlib import Path
from html import escape as html_escape
from utils.operations_assistant import render_operations_assistant
from utils.page_header import render_page_title
from utils.kakao_station_map import render_kakao_station_map, render_priority_station_list
from utils.suspect_usage import get_suspect_raw_source_signature, load_suspect_usage_from_raw_od


# ==========================================
# 1. 파일 경로 설정
# ==========================================

BASE_DIR = Path(__file__).resolve().parents[1]

OUTPUT_DIR = BASE_DIR / "outputs"
DATA_DIR = BASE_DIR / "data" / "daily_data"
OPERATIONS_DIR = OUTPUT_DIR / "operation_results"
LEGACY_OPERATIONS_PATH = OUTPUT_DIR / "operations_result.csv"

STATION_PATH = BASE_DIR / "data" / "서울시 따릉이대여소 마스터 정보.csv"


# ==========================================
# 공통 표시 및 카카오 지도 함수
# ==========================================

DASHBOARD_STATUS_COLORS = {
    "공급부족": "#DC5050",
    "적정": "#447DB8",
    "공급과잉": "#C99B22",
    "범위 산출 불가": "#999999",
}

# 선택 정류소 상세의 지정된 구간에만 간격을 적용한다.
st.markdown(
    """
    <style>
    /* 2번: KPI 아래 첫 섹션까지의 기본 간격을 절반 수준으로 줄인다. */
    .st-key-detail_recent_comparison {
        margin-top: -0.5rem !important;
        margin-bottom: -0.5rem !important;
    }

    /* 두 섹션 내부의 자동 간격은 끄고, 제목·표·구분선에 개별 간격을 준다. */
    .st-key-detail_recent_comparison [data-testid="stVerticalBlock"],
    .st-key-detail_future_forecast [data-testid="stVerticalBlock"] {
        gap: 0 !important;
        row-gap: 0 !important;
    }

    /* 3번·5번: 구분선의 위아래 간격을 각각 0.5rem으로 고정한다. */
    .detail-section-divider {
        border: 0;
        border-top: 1px solid var(--st-border-color);
        margin: 0.5rem 0 !important;
    }

    /* 구분선과 제목, 제목과 표 사이에 일정한 간격을 남긴다. */
    .detail-section-title {
        margin: 1rem 1rem 1.5rem 0 !important;
        padding: 0 !important;
        line-height: 1.35 !important;
        font-weight: 600;
    }

    /* 최근 이용량 비교 제목은 선택 박스와 독립적으로 스타일을 관리한다. */
    .detail-comparison-heading {
        font-size: 1.05rem !important;
        font-weight: 650 !important;
        line-height: 1.35 !important;
    }

    /* 선택 정류소 상세 KPI와 구분선 사이의 간격 */
    .st-key-detail_kpi_divider_spacing [data-testid="stVerticalBlock"] {
        gap: 0.2rem !important;
        row-gap: 0.2rem !important;
    }

    .st-key-detail_kpi_divider_spacing hr {
        margin: 0.15rem 0 !important;
    }

    /* 최근 이용량 비교 제목 행과 비교 표 사이 간격은 표 컨테이너에서만 관리한다. */
    .st-key-detail_recent_comparison_table {
        padding-top: 0.5rem !important;
    }

    /* 최근 시간 선택 박스는 전용 컨테이너 안에서 별도로 크기를 관리한다. */
    .st-key-detail_comparison_window_control [data-testid="stSelectbox"] {
        font-size: 0.95rem !important;
    }

    /* 최신 React Aria 기반 Streamlit 선택 박스 */
    .st-key-detail_comparison_window_control .react-aria-Group,
    .st-key-detail_comparison_window_control [role="group"] {
        height: 1.85rem !important;
        min-height: 1.85rem !important;
        max-height: 1.85rem !important;
        box-sizing: border-box !important;
    }

    .st-key-detail_comparison_window_control .react-aria-Group input,
    .st-key-detail_comparison_window_control [role="group"] input {
        height: 100% !important;
        min-height: 0 !important;
        padding: 0 0.45rem !important;
        font-size: 0.95rem !important;
        line-height: 1.2 !important;
        box-sizing: border-box !important;
    }

    .st-key-detail_comparison_window_control .react-aria-Group button,
    .st-key-detail_comparison_window_control [role="group"] button {
        height: 100% !important;
        min-height: 0 !important;
        padding-top: 0 !important;
        padding-bottom: 0 !important;
        box-sizing: border-box !important;
    }

    /* 구버전 Streamlit BaseWeb 구현 호환 */
    .st-key-detail_comparison_window_control [data-baseweb="select"],
    .st-key-detail_comparison_window_control [data-baseweb="select"] > div,
    .st-key-detail_comparison_window_control [data-baseweb="select"] [role="combobox"] {
        height: 1.85rem !important;
        min-height: 1.85rem !important;
        max-height: 1.85rem !important;
        padding-top: 0 !important;
        padding-bottom: 0 !important;
        font-size: 0.95rem !important;
        line-height: 1.2 !important;
        box-sizing: border-box !important;
    }

    /* 선택 정류소 KPI의 숫자·라벨을 약 2pt 줄여 5개 항목을 안정적으로 배치한다. */
    .st-key-selected_station_detail_card [data-testid="stMetricValue"] {
        font-size: 1.72rem !important;
    }
    .st-key-selected_station_detail_card [data-testid="stMetricLabel"] {
        font-size: 0.78rem !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def format_count(value):
    if pd.isna(value):
        return "정보 없음"
    return f"{float(value):,.0f}"


def render_compact_detail_heading(label: str):
    """선택 정류소 상세의 구분선과 소제목을 일정한 간격으로 표시한다."""
    st.markdown('<hr class="detail-section-divider">', unsafe_allow_html=True)
    st.markdown(
        f'<p class="detail-section-title">{html_escape(label)}</p>',
        unsafe_allow_html=True,
    )


def get_kakao_map_js_key():
    """환경변수 또는 Streamlit secrets에서 카카오 지도 JavaScript 키를 읽는다."""
    key = os.getenv("KAKAO_MAP_JAVASCRIPT_KEY", "").strip()
    if key:
        return key
    try:
        return str(st.secrets["KAKAO_MAP_JAVASCRIPT_KEY"]).strip()
    except Exception:
        return ""


def build_dashboard_marker_data(map_data: pd.DataFrame) -> list[dict]:
    """카카오 지도 컴포넌트에 전달할 정류소 마커 데이터를 만든다."""
    marker_data = []
    for values in map_data.to_dict("records"):
        marker_data.append({
            "id": str(values["대여소_ID"]),
            "lat": float(values["위도"]),
            "lng": float(values["경도"]),
            "status": str(values.get("운영상태", "범위 산출 불가")),
            "stock": None if pd.isna(values.get("종료재고")) else float(values["종료재고"]),
            "lower": None if pd.isna(values.get("하한재고")) else float(values["하한재고"]),
            "upper": None if pd.isna(values.get("상한재고")) else float(values["상한재고"]),
            "shortage": None if pd.isna(values.get("부족량")) else float(values["부족량"]),
            "surplus": None if pd.isna(values.get("초과량")) else float(values["초과량"]),
            "suspect_usage_count": (
                None if pd.isna(values.get("점검의심이용건수"))
                else int(values["점검의심이용건수"])
            ),
        })
    return marker_data




def render_recent_usage_comparison(
    actual_rental,
    predicted_rental,
    actual_return,
    predicted_return,
) -> None:
    """최근 대여·반납 실제 이용량과 모델 예측량을 표로 비교한다."""
    values = [actual_rental, predicted_rental, actual_return, predicted_return]
    numeric_values = [
        pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
        for value in values
    ]
    if any(pd.isna(value) for value in numeric_values):
        st.info("최근 이용량 비교 데이터를 확인할 수 없습니다.")
        return

    rental_actual, rental_predicted, return_actual, return_predicted = map(
        float, numeric_values
    )

    rows = []
    for label, actual, predicted in [
        ("대여량", rental_actual, rental_predicted),
        ("반납량", return_actual, return_predicted),
    ]:
        change_amount = actual - predicted
        change_rate = (
            f"{change_amount / predicted * 100:+.1f}%"
            if predicted > 0 else "—"
        )
        rows.append({
            "구분": label,
            "실제 이용량(대)": f"{actual:,.0f}",
            "모델 예측량(대)": f"{predicted:,.1f}",
            "변화량(대)": f"{change_amount:+,.1f}",
            "예측 대비 변화율(%)": change_rate,
        })

    comparison_table = pd.DataFrame(rows)
    st.dataframe(
        comparison_table,
        hide_index=True,
        use_container_width=True,
        height=115,
    )
    st.markdown('<div style="height:8px;"></div>', unsafe_allow_html=True)

def create_usage_comparison_figure(
    actual_hourly: pd.DataFrame,
    reference_hour: int,
):
    """시간대별 모델 예측량과 기준 시각 이전 실제 이용량을 대여·반납으로 비교한다."""
    hourly = actual_hourly.groupby("기준_시간대").agg(
        실제대여=("대여실적", "sum"),
        예측대여=("대여예측건수", "sum"),
        실제반납=("반납실적", "sum"),
        예측반납=("반납예측건수", "sum"),
    ).reindex(range(24)).rename_axis("기준_시간대").reset_index()

    observed = hourly["기준_시간대"] < int(reference_hour)
    hours = [f"{int(hour):02d}:00" for hour in hourly["기준_시간대"]]
    actual_rentals = hourly["실제대여"].where(observed)
    actual_returns = hourly["실제반납"].where(observed)

    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.15,
        subplot_titles=("시간대별 대여량", "시간대별 반납량"),
    )

    fig.add_trace(go.Bar(
        x=hours, y=actual_rentals, name="실제 이용량",
        marker=dict(color="#E45756", opacity=0.72),
        legendgroup="actual", showlegend=True,
        hovertemplate="실제 대여: %{y:,.0f}대<extra></extra>",
    ), row=1, col=1)
    fig.add_trace(go.Scatter(
        x=hours, y=hourly["예측대여"], name="모델 예측량",
        mode="lines+markers", line=dict(color="#D97706", width=2.5, dash="dash"),
        marker=dict(size=5), legendgroup="prediction", showlegend=True,
        hovertemplate="모델 예측 대여량: %{y:,.1f}대<extra></extra>",
    ), row=1, col=1)
    fig.add_trace(go.Bar(
        x=hours, y=actual_returns, name="실제 이용량",
        marker=dict(color="#2563EB", opacity=0.72),
        legendgroup="actual", showlegend=False,
        hovertemplate="실제 반납: %{y:,.0f}대<extra></extra>",
    ), row=2, col=1)
    fig.add_trace(go.Scatter(
        x=hours, y=hourly["예측반납"], name="모델 예측량",
        mode="lines+markers", line=dict(color="#D97706", width=2.5, dash="dash"),
        marker=dict(size=5), legendgroup="prediction", showlegend=False,
        hovertemplate="모델 예측 반납량: %{y:,.1f}대<extra></extra>",
    ), row=2, col=1)

    reference_color = "#0F766E"
    reference_label = "기준시각"

    for row in (1, 2):
        fig.add_vline(
            x=f"{int(reference_hour):02d}:00",
            line_dash="dot",
            line_color=reference_color,
            line_width=2,
            row=row,
            col=1,
        )

    # 기준 시각 세로선을 범례에도 표시하기 위한 더미 항목
    fig.add_trace(go.Scatter(
        x=[None], y=[None], name=reference_label, mode="lines",
        line=dict(color=reference_color, width=2, dash="dot"),
        showlegend=True, hoverinfo="skip",
    ), row=1, col=1)

    tick_hours = [f"{hour:02d}:00" for hour in range(0, 24, 2)]
    fig.update_layout(
        height=560,
        margin=dict(l=8, r=12, t=70, b=8),
        barmode="group",
        hovermode="x unified",
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.06,
            xanchor="right",
            x=1,
        ),
    )
    fig.update_yaxes(title_text="대여량(대)", rangemode="tozero", row=1, col=1)
    fig.update_yaxes(title_text="반납량(대)", rangemode="tozero", row=2, col=1)
    for row in (1, 2):
        fig.update_xaxes(
            title_text="",
            type="category",
            tickmode="array",
            tickvals=tick_hours,
            ticktext=tick_hours,
            showticklabels=True,
            row=row,
            col=1,
        )
    return fig



def get_file_signature(path: Path) -> tuple[str, int | None, int | None]:
    """캐시 갱신을 위해 파일 경로·수정 시각·크기를 반환한다."""
    try:
        stat = path.stat()
        return str(path), stat.st_mtime_ns, stat.st_size
    except OSError:
        return str(path), None, None


def get_rolling_source_signature(target_date: str) -> tuple:
    """운영 분석 입력 파일이 바뀌면 Streamlit 캐시가 갱신되도록 서명을 만든다."""
    date_folder = OUTPUT_DIR / "inventory_history" / target_date
    prediction_candidates = [
        OUTPUT_DIR / "prediction_results" / f"demand_forecast_{target_date}.csv",
        OUTPUT_DIR / f"demand_forecast_{target_date}.csv",
    ]
    prediction_path = next((item for item in prediction_candidates if item.exists()), prediction_candidates[0])
    source_paths = [
        BASE_DIR / "data" / "daily_data" / f"tpss_bcycl_od_statnhm_{target_date}.csv",
        prediction_path,
        date_folder / f"{target_date}_gangseo_station_history.csv",
        OPERATIONS_DIR / f"operations_result_{target_date}.csv",
        STATION_PATH,
    ]
    return tuple(get_file_signature(item) for item in source_paths)


@st.cache_data(show_spinner=False)
def load_rolling_analysis(
    target_date: str,
    reference_hour: int,
    recent_window_hours: int,
    source_signature: tuple,
):
    """기준 시각별 운영 분석 결과를 캐시해 지도 클릭 등으로 인한 재계산을 방지한다."""
    from utils.rolling_operations import run_rolling_operations

    return run_rolling_operations(
        target_date=target_date,
        reference_hour=reference_hour,
        recent_window_hours=recent_window_hours,
    )


def get_latest_operations_path():
    """날짜별 운영 결과 중 기준일이 가장 최신인 파일을 선택한다."""
    candidates = sorted(OPERATIONS_DIR.glob("operations_result_*.csv"))
    if candidates:
        return candidates[-1]
    if LEGACY_OPERATIONS_PATH.exists():
        return LEGACY_OPERATIONS_PATH
    raise FileNotFoundError(
        f"운영 결과 파일을 찾을 수 없습니다: {OPERATIONS_DIR}"
    )


# ==========================================
# 2. 데이터 불러오기
# ==========================================

@st.cache_data
def load_data(
    operations_path: str,
    operations_mtime_ns: int,
    operations_size: int,
):
    """운영 결과와 정류소 마스터를 불러온다."""
    operations = pd.read_csv(operations_path, encoding="utf-8-sig")
    stations = pd.read_csv(STATION_PATH, encoding="cp949")
    return operations, stations


try:
    operations_path = get_latest_operations_path()
    operations_stat = operations_path.stat()
    operations, stations = load_data(
        str(operations_path),
        operations_stat.st_mtime_ns,
        operations_stat.st_size,
    )

except FileNotFoundError as e:
    st.error(f"데이터 파일을 찾을 수 없습니다: {e}")
    st.stop()

except Exception as e:
    st.error(f"데이터 불러오기 오류: {e}")
    st.stop()


# ==========================================
# 3. 데이터 전처리
# ==========================================

operations["기준_날짜"] = pd.to_datetime(
    operations["기준_날짜"]
)

operations["대여소_ID"] = (
    operations["대여소_ID"].astype(str).str.strip()
)

# 이전 운영 결과 파일과의 호환성을 위해 우선순위 컬럼이 없으면 결측으로 둔다.
for priority_col in ["부족우선순위", "초과우선순위"]:
    if priority_col not in operations.columns:
        operations[priority_col] = pd.NA

stations["대여소_ID"] = (
    stations["대여소_ID"].astype(str).str.strip()
)

stations["위도"] = pd.to_numeric(
    stations["위도"], errors="coerce"
)

stations["경도"] = pd.to_numeric(
    stations["경도"], errors="coerce"
)

stations = stations.dropna(
    subset=["위도", "경도"]
)

stations = stations.drop_duplicates(
    "대여소_ID"
)


# ==========================================
# 4. 상단 제목 및 운영 분석 조건 선택
# ==========================================

district_options = [
    "강서구", "강남구", "강북구", "관악구", "구로구",
    "강동구", "광진구", "금천구", "노원구", "도봉구",
    "동대문구", "동작구", "마포구", "서대문구", "서초구",
    "성동구", "성북구", "송파구", "양천구", "영등포구",
    "용산구", "은평구", "종로구", "중구", "중랑구"
]

# 대시보드와 운영 현황 페이지가 최근 실적 비교 구간을 공유한다.
recent_window_state_key = "dashboard_recent_window_hours"
shared_recent_window_key = "operations_analysis_recent_window_hours"
st.session_state.setdefault(shared_recent_window_key, 3)
if st.session_state[shared_recent_window_key] not in (2, 3):
    st.session_state[shared_recent_window_key] = 3
st.session_state[recent_window_state_key] = int(
    st.session_state[shared_recent_window_key]
)


def sync_dashboard_recent_window() -> None:
    """대시보드에서 선택한 실적 비교 구간을 공통 세션 상태에 저장한다."""
    st.session_state[shared_recent_window_key] = int(
        st.session_state[recent_window_state_key]
    )


recent_window_hours = int(st.session_state[shared_recent_window_key])

title_col, district_col, date_col, hour_col = st.columns(
    [2.0, 0.9, 1.0, 1.0]
)

with title_col:
    render_page_title("운영 대시보드")

with district_col:
    st.markdown("**행정구 선택**")
    selected_district = st.selectbox(
        "행정구 선택",
        district_options,
        label_visibility="collapsed",
        key="dashboard_district",
    )

if selected_district != "강서구":
    st.info(f"{selected_district} 대시보드는 아직 준비 중입니다.")
    st.stop()

date_options = sorted(operations["기준_날짜"].dt.date.unique())
with date_col:
    st.markdown("**운영 기준일**")
    selected_date = st.selectbox(
        "운영 기준일",
        date_options,
        index=len(date_options) - 1,
        label_visibility="collapsed",
        key="dashboard_date",
    )

day_data = operations[operations["기준_날짜"].dt.date == selected_date].copy()
available_hours = set(pd.to_numeric(day_data["기준_시간대"], errors="coerce").dropna().astype(int))
reference_hour_options = sorted(available_hours)
if not reference_hour_options:
    st.error("선택한 날짜의 운영 시간대 데이터가 없습니다.")
    st.stop()

with hour_col:
    st.markdown("**운영 기준 시각**")
    selected_hour = st.selectbox(
        "운영 기준 시각",
        reference_hour_options,
        index=reference_hour_options.index(9) if 9 in reference_hour_options else 0,
        format_func=lambda value: f"{int(value):02d}:00",
        label_visibility="collapsed",
        key="dashboard_reference_hour",
    )

# AI 운영 분석 페이지와 동일한 분석 조건을 공유한다.
target_date_label = selected_date.strftime("%Y%m%d")
st.session_state["operations_analysis_target_date"] = target_date_label
st.session_state["operations_analysis_reference_hour"] = int(selected_hour)

# 기준 시각별 실제 실적 분석을 우선 시도한다. 필요한 파일이 아직 없으면
# 기존 일일 운영 결과를 대체 표시하고, 사용자에게 분석 모드를 명확히 알린다.
rolling_result = None
rolling_error = None
try:
    rolling_signature = get_rolling_source_signature(target_date_label)
    rolling_result = load_rolling_analysis(
        target_date_label,
        int(selected_hour),
        int(recent_window_hours),
        rolling_signature,
    )
except Exception as exc:
    rolling_error = str(exc)

if rolling_result is not None:
    hour_data = rolling_result["station_result"].copy()
    hour_data["종료재고"] = hour_data["현재추정재고"]

    # 기존 우선 확인 목록의 정렬 기준을 유지한다.
    shortage_mask = hour_data["운영상태"].eq("공급부족")
    surplus_mask = hour_data["운영상태"].eq("공급과잉")
    hour_data["부족우선순위"] = pd.NA
    hour_data["초과우선순위"] = pd.NA
    hour_data.loc[shortage_mask, "부족우선순위"] = (
        hour_data.loc[shortage_mask, "부족량"]
        .rank(method="dense", ascending=False)
    )
    hour_data.loc[surplus_mask, "초과우선순위"] = (
        hour_data.loc[surplus_mask, "초과량"]
        .rank(method="dense", ascending=False)
    )

    st.session_state["dashboard_rolling_summary"] = rolling_result["summary"]
    st.session_state["dashboard_rolling_available"] = True
else:
    hour_data = day_data[day_data["기준_시간대"] == selected_hour].copy()
    st.session_state["dashboard_rolling_summary"] = None
    st.session_state["dashboard_rolling_available"] = False
    st.warning(
        "선택한 날짜의 시간대별 실적 분석을 아직 불러오지 못했습니다. "
        "현재는 기존 일일 예측 시뮬레이션 결과를 표시합니다. 실제 실적 반영은 적용되지 않은 상태입니다."
    )
    if rolling_error:
        with st.expander("실적 분석 데이터 연결 상세 사유"):
            st.caption(rolling_error)

# Rolling station_result는 기준 시각별 정류소 상태만 포함하므로 시간 키를 보완한다.
if "기준_시간대" not in hour_data.columns:
    hour_data["기준_시간대"] = int(selected_hour)

# 날짜별 원본 OD를 직접 분석해 고장 의심 기록을 정류소·시간대 단위로 연결한다.
# 대시보드와 운영 현황은 출발시간·시작 정류소 기준을 사용한다.
# 원천 OD에는 개별 자전거 ID가 없으므로 '고장 대수'가 아닌 '고장 의심(건)'으로 표시한다.
suspect_source_signature = get_suspect_raw_source_signature(
    target_date_label,
    DATA_DIR,
    STATION_PATH,
)
suspect_hourly, suspect_data_available, suspect_source_name = load_suspect_usage_from_raw_od(
    target_date_label,
    suspect_source_signature,
    DATA_DIR,
    STATION_PATH,
    aggregation_basis="출발시간",
)

if suspect_data_available:
    # 대여소 ID의 공백·숫자형 변환과 시간 컬럼 타입 차이로 조인이 누락되지 않도록
    # 화면 데이터와 의심 집계 데이터에 동일한 임시 조인 키를 만든다.
    hour_data = hour_data.copy()
    suspect_hourly = suspect_hourly.copy()
    hour_data["_suspect_station_key"] = (
        hour_data["대여소_ID"].astype("string").str.strip()
        .str.replace(r"\.0$", "", regex=True)
    )
    suspect_hourly["_suspect_station_key"] = (
        suspect_hourly["대여소_ID"].astype("string").str.strip()
        .str.replace(r"\.0$", "", regex=True)
    )
    hour_data["_suspect_hour_key"] = pd.to_numeric(
        hour_data["기준_시간대"], errors="coerce"
    ).astype("Int64")
    suspect_hourly["_suspect_hour_key"] = pd.to_numeric(
        suspect_hourly["기준_시간대"], errors="coerce"
    ).astype("Int64")

    hour_data = hour_data.merge(
        suspect_hourly[[
            "_suspect_station_key",
            "_suspect_hour_key",
            "점검의심이용건수",
        ]],
        left_on=["_suspect_station_key", "_suspect_hour_key"],
        right_on=["_suspect_station_key", "_suspect_hour_key"],
        how="left",
        validate="one_to_one",
    )
    hour_data.drop(
        columns=["_suspect_station_key", "_suspect_hour_key"],
        inplace=True,
    )
    hour_data["점검의심이용건수"] = (
        pd.to_numeric(hour_data["점검의심이용건수"], errors="coerce")
        .fillna(0)
        .astype(int)
    )
else:
    hour_data["점검의심이용건수"] = pd.NA
    if suspect_source_name:
        st.warning(f"고장 의심 데이터 연결 실패: {suspect_source_name}")


# ==========================================
# 7. 운영 현황 페이지 이동 함수
# ==========================================

def go_to_operations(
    filter_value="전체",
    station_id=None
):

    if station_id is None:

        st.session_state["ops_tab_request"] = "정류소 목록"

        st.session_state["ops_filter_request"] = filter_value

        st.session_state["ops_station_request"] = None

    else:

        st.session_state["ops_tab_request"] = "정류소 상세"

        st.session_state["ops_filter_request"] = "전체"

        st.session_state["ops_station_request"] = str(station_id)

    st.session_state["ops_date_request"] = selected_date

    st.switch_page("pages/operations.py")


# ==========================================
# 8. 핵심 KPI 계산
# ==========================================

shortage_count = int(hour_data["운영상태"].eq("공급부족").sum())
surplus_count = int(hour_data["운영상태"].eq("공급과잉").sum())
normal_count = int(hour_data["운영상태"].eq("적정").sum())

valid = hour_data["하한재고"].notna() & hour_data["상한재고"].notna()
valid_count = int(valid.sum())
satisfaction_rate = normal_count / valid_count * 100 if valid_count > 0 else 0

if rolling_result is not None:
    future_result = rolling_result["hourly_result"]
    unmet_demand = (
        pd.to_numeric(future_result["예상_미충족대여수요"], errors="coerce").fillna(0).sum()
        if not future_result.empty else 0
    )
    unmet_demand_label = "미충족 대여 수요"
else:
    unmet_demand_col = next(
        (col for col in ("예상_미충족대여수요", "미충족대여수요") if col in hour_data.columns),
        None,
    )
    if unmet_demand_col is None:
        st.error("운영 결과 파일에 미충족 대여 수요 컬럼이 없습니다.")
        st.stop()
    unmet_demand = pd.to_numeric(
        hour_data[unmet_demand_col], errors="coerce"
    ).fillna(0).sum()
    unmet_demand_label = "미충족 수요"


# 현재 운영 현황 KPI 공통 색상 순서 (1~6번, 이후 반복)
KPI_ACCENT_COLORS = (
    "#447DB8",  # 1. 파랑
    "#269B83",  # 2. 초록
    "#E3A23B",  # 3. 노랑
    "#8B6BCB",  # 4. 보라
    "#E45756",  # 5. 코랄
    "#64748B",  # 6. 슬레이트
)

def show_status_kpi_card(label, value, accent_color, help_text=None):
    """운영 현황 KPI를 색상 강조 카드로 표시한다."""
    tooltip = (
        f' title="{html_escape(str(help_text), quote=True)}"'
        if help_text else ""
    )
    st.markdown(
        f"""
        <div{tooltip} style="
            background: var(--secondary-background-color);
            color: var(--text-color);
            border: 1px solid rgba(128,128,128,0.25);
            border-top: 4px solid {accent_color};
            border-radius: 8px;
            padding: 12px 10px;
            min-height: 105px;
            box-sizing: border-box;
        ">
            <div style="
                font-size: 0.82rem;
                line-height: 1.35;
                opacity: 0.72;
                margin-bottom: 11px;
            ">
                {html_escape(str(label))}
            </div>
            <div style="
                font-size: 1.45rem;
                line-height: 1.25;
                font-weight: 700;
                color: {accent_color};
            ">
                {html_escape(str(value))}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ==========================================
# 9. 현재 운영 현황 KPI
# ==========================================

st.subheader("현재 운영 현황")
kpi1, kpi2, kpi3, kpi4 = st.columns(4, gap="medium")

with kpi1:
    show_status_kpi_card("공급부족 정류소", f"{shortage_count:,}개", KPI_ACCENT_COLORS[4])
    if st.button("부족 정류소 확인", key="go_shortage", use_container_width=True):
        go_to_operations("공급부족")

with kpi2:
    show_status_kpi_card("적정 정류소", f"{normal_count:,}개", KPI_ACCENT_COLORS[0])
    if st.button("적정 정류소 확인", key="go_normal", use_container_width=True):
        go_to_operations("적정")

with kpi3:
    show_status_kpi_card("공급과잉 정류소", f"{surplus_count:,}개", KPI_ACCENT_COLORS[2])
    if st.button("과잉 정류소 확인", key="go_surplus", use_container_width=True):
        go_to_operations("공급과잉")

with kpi4:
    show_status_kpi_card(
        unmet_demand_label,
        f"{unmet_demand:,.0f}대",
        KPI_ACCENT_COLORS[3],
        help_text=(
            "기준 시각 이후 예측 수요 중 재고 부족으로 충족하지 못할 것으로 예상되는 수량입니다."
            if rolling_result is not None else "기존 일일 운영 시뮬레이션 결과입니다."  
        ),
    )


st.divider()

# ==========================================
# 10. 정류소 지도 데이터 결합 및 카카오 지도
# ==========================================

map_data = hour_data.merge(
    stations[["대여소_ID", "위도", "경도"]],
    on="대여소_ID",
    how="left",
    validate="many_to_one",
)
map_data = map_data.dropna(subset=["위도", "경도"]).copy()

if "dashboard_map_reset_counter" not in st.session_state:
    st.session_state["dashboard_map_reset_counter"] = 0
if "dashboard_selected_station_id" not in st.session_state:
    st.session_state["dashboard_selected_station_id"] = None

priority_component_key = "dashboard_priority_station_list"
priority_component_state = st.session_state.get(priority_component_key, {})
if isinstance(priority_component_state, dict):
    priority_selected_id = priority_component_state.get("selected_station_id")
    priority_hovered_id = priority_component_state.get("hovered_station_id")
else:
    priority_selected_id = None
    priority_hovered_id = None

if priority_selected_id:
    st.session_state["dashboard_selected_station_id"] = str(priority_selected_id)
st.session_state["dashboard_hovered_station_id"] = (
    str(priority_hovered_id) if priority_hovered_id else None
)

valid_map_station_ids = set(map_data["대여소_ID"].astype(str))
current_selected_station_id = st.session_state.get("dashboard_selected_station_id")
if current_selected_station_id and str(current_selected_station_id) not in valid_map_station_ids:
    st.session_state["dashboard_selected_station_id"] = None
    st.session_state["dashboard_map_reset_counter"] += 1
    if isinstance(priority_component_state, dict):
        priority_component_state["selected_station_id"] = None
    priority_selected_id = None

if st.session_state.get("dashboard_hovered_station_id") not in valid_map_station_ids:
    st.session_state["dashboard_hovered_station_id"] = None
    if isinstance(priority_component_state, dict):
        priority_component_state["hovered_station_id"] = None

map_col, priority_col = st.columns([1.35, 1], gap="large")

with map_col:
    st.markdown(
        '<div style="display:flex; align-items:baseline; flex-wrap:nowrap; gap:0.65rem; margin-bottom:0.75rem;">'
        '<h3 style="font-size:1.75rem; font-weight:600; line-height:1.2; margin:0; white-space:nowrap;">정류소 현황 지도</h3>'
        '<span style="font-size:0.82rem; color:var(--st-secondary-text-color, currentColor); white-space:nowrap;">'
        '지도의 마커를 클릭하여 해당 정류소의 상세 정보를 확인할 수 있습니다.'
        '</span>'
        '</div>',
        unsafe_allow_html=True,
    )
    js_key = get_kakao_map_js_key()
    if map_data.empty:
        st.warning("지도에 표시할 정류소 좌표가 없습니다.")
    elif not js_key:
        st.warning(
            "카카오 지도 JavaScript 키가 없습니다. "
            "KAKAO_MAP_JAVASCRIPT_KEY 환경변수 또는 .streamlit/secrets.toml을 확인해 주세요."
        )
    else:
        try:
            marker_payload = build_dashboard_marker_data(map_data)
            map_instance_key = (
                f"dashboard_kakao_station_map_{st.session_state['dashboard_map_reset_counter']}"
            )
            selected_map_station_id = render_kakao_station_map(
                marker_data=marker_payload,
                js_key=js_key,
                selected_station_id=st.session_state.get("dashboard_selected_station_id"),
                hovered_station_id=st.session_state.get("dashboard_hovered_station_id"),
                key=map_instance_key,
            )
            if selected_map_station_id:
                st.session_state["dashboard_selected_station_id"] = str(selected_map_station_id)
        except Exception as error:
            st.error(f"카카오 지도 생성 오류: {error}")

# ==========================================
# 13. 우선 확인 정류소 / 선택 정류소 상세
# ==========================================

# 우선 확인 목록과 AI 운영 도우미에 전달할 데이터를 화면 상태와 무관하게 계산한다.
shortage_data = (
    hour_data[hour_data["운영상태"] == "공급부족"]
    .sort_values(
        ["부족우선순위", "부족량"],
        ascending=[True, False],
        na_position="last",
    )
    .head(10)
    .copy()
)
shortage_data["확인 사유"] = "공급부족"

surplus_data = (
    hour_data[hour_data["운영상태"] == "공급과잉"]
    .sort_values(
        ["초과우선순위", "초과량"],
        ascending=[True, False],
        na_position="last",
    )
    .head(10)
    .copy()
)
surplus_data["확인 사유"] = "공급과잉"
priority_data = pd.concat([shortage_data, surplus_data], ignore_index=True)

selected_station_id = st.session_state.get("dashboard_selected_station_id")
selected_station_rows = pd.DataFrame()
if selected_station_id:
    selected_station_rows = hour_data[
        hour_data["대여소_ID"].astype(str).eq(str(selected_station_id))
    ]
    if selected_station_rows.empty:
        st.session_state["dashboard_selected_station_id"] = None
        st.session_state["dashboard_map_reset_counter"] += 1
        selected_station_id = None

with priority_col:
    if selected_station_id:
        selected_station = selected_station_rows.iloc[0]
        selected_status = str(selected_station.get("운영상태", "정보 없음"))

        detail_header, back_col = st.columns(
            [4.5, 1.5],
            vertical_alignment="center",
        )
        with detail_header:
            st.subheader("선택 정류소 상세")
        with back_col:
            if st.button(
                "← 목록으로 돌아가기",
                key=f"back_to_priority_{selected_station_id}",
                use_container_width=True,
            ):
                st.session_state["dashboard_selected_station_id"] = None
                st.session_state["dashboard_hovered_station_id"] = None
                priority_state = st.session_state.get(priority_component_key)
                if isinstance(priority_state, dict):
                    priority_state["selected_station_id"] = None
                    priority_state["hovered_station_id"] = None
                st.session_state["dashboard_map_reset_counter"] += 1
                st.rerun()

        with st.container(border=True, key="selected_station_detail_card"):
            station_name_col = next(
                (column for column in ["대여소명", "대여소_명", "정류소명", "대여소명칭"] if column in stations.columns),
                None,
            )
            selected_station_name = str(selected_station_id)
            if station_name_col:
                name_rows = stations.loc[
                    stations["대여소_ID"].astype(str).eq(str(selected_station_id)),
                    station_name_col,
                ]
                if not name_rows.empty and pd.notna(name_rows.iloc[0]):
                    selected_station_name = str(name_rows.iloc[0]).strip() or selected_station_name

            station_status_col, recent_window_col = st.columns(
                [5.2, 1.3],
                vertical_alignment="center",
            )
            with station_status_col:
                st.markdown(
                    '<div style="display:flex; align-items:baseline; flex-wrap:wrap; gap:12px; margin-bottom:0.35rem;">'
                    f'<span style="font-size:1.35rem; font-weight:700;">{html_escape(selected_station_name)}</span>'
                    f'<span style="font-size:0.9rem; color:var(--st-secondary-text-color);">운영 상태: {html_escape(selected_status)}</span>'
                    '</div>',
                    unsafe_allow_html=True,
                )
            with recent_window_col:
                with st.container(key="detail_comparison_window_control", gap=None):
                    st.selectbox(
                        "최근 이용량 비교 시간",
                        [2, 3],
                        format_func=lambda value: f"최근 {value}시간",
                        label_visibility="collapsed",
                        key=recent_window_state_key,
                        on_change=sync_dashboard_recent_window,
                    )

            with st.container(key="detail_kpi_divider_spacing", gap=None):
                detail_kpis = st.columns(5, gap="small")
                detail_kpis[0].metric(
                    "현재 추정 재고",
                    f"{float(selected_station.get('종료재고', 0)):,.0f}대",
                )
                lower_value = selected_station.get("하한재고")
                upper_value = selected_station.get("상한재고")
                detail_kpis[1].metric(
                    "적정 하한",
                    "정보 없음" if pd.isna(lower_value) else f"{float(lower_value):,.0f}대",
                )
                detail_kpis[2].metric(
                    "적정 상한",
                    "정보 없음" if pd.isna(upper_value) else f"{float(upper_value):,.0f}대",
                )

                if selected_status == "공급부족":
                    imbalance_label = "부족량"
                    imbalance_value = selected_station.get("부족량", 0)
                elif selected_status == "공급과잉":
                    imbalance_label = "초과량"
                    imbalance_value = selected_station.get("초과량", 0)
                else:
                    imbalance_label = "부족·초과량"
                    imbalance_value = 0
                if pd.isna(imbalance_value):
                    imbalance_value = 0
                detail_kpis[3].metric(
                    imbalance_label,
                    f"{float(imbalance_value):,.0f}대",
                )
                suspect_count = selected_station.get("점검의심이용건수")
                detail_kpis[4].metric(
                    "고장 의심",
                    f"{float(suspect_count):,.0f}건" if pd.notna(suspect_count) else "0건",
                    help=(
                        "정상 이용 기준에서 벗어난 의심 이용 기록의 집계 건수입니다. "
                        "고유 고장 차량 수나 확정 고장 대수를 뜻하지 않습니다."
                    ),
                )
                st.divider()

            with st.container(key="detail_recent_comparison", gap=None):
                comparison_start_hour = max(0, int(selected_hour) - int(recent_window_hours))
                if int(selected_hour) > comparison_start_hour:
                    comparison_end_hour = int(selected_hour) - 1
                    comparison_range_label = (
                        f"{comparison_start_hour:02d}:00~{comparison_end_hour:02d}:59"
                    )
                else:
                    comparison_range_label = "비교 가능한 완료 시간대 없음"

                st.markdown(
                    '<div style="display:flex; align-items:baseline; flex-wrap:wrap; '
                    'gap:12px; margin:0.35rem 0 0.45rem 0;">'
                    '<span class="detail-comparison-heading">최근 이용량 비교</span>'
                    f'<span style="font-size:0.82rem; color:var(--st-secondary-text-color);">'
                    f'비교 범위: {html_escape(comparison_range_label)}</span></div>',
                    unsafe_allow_html=True,
                )

                # 비교 표는 제목 및 비교 범위 바로 아래에 배치한다.
                with st.container(key="detail_recent_comparison_table", gap=None):
                    if rolling_result is not None and int(selected_hour) > 0:
                        render_recent_usage_comparison(
                            selected_station.get("최근_대여실적"),
                            selected_station.get("최근_대여예측"),
                            selected_station.get("최근_반납실적"),
                            selected_station.get("최근_반납예측")
                        )
                    else:
                        st.info("기준 시각 이전에 완료된 시간대가 없어 최근 이용량을 비교할 수 없습니다.")

                station_future = rolling_result["hourly_result"].copy()
                if not station_future.empty:
                    station_future = station_future[
                        station_future["대여소_ID"].astype(str).eq(str(selected_station_id))
                        & station_future["기준_시간대"].ge(int(selected_hour))
                        & station_future["기준_시간대"].lt(min(24, int(selected_hour) + 3))
                    ].sort_values("기준_시간대")

                if not station_future.empty:
                    with st.container(key="detail_future_forecast", gap=None):
                        render_compact_detail_heading("최근 3시간 예상 이용 및 재고")
                        future_detail = station_future[[
                            "기준_시간대", "대여예측건수", "반납예측건수",
                            "종료재고", "하한재고", "상한재고", "부족량", "초과량",
                        ]].rename(columns={
                            "기준_시간대": "시간",
                            "대여예측건수": "예상 대여량(대)",
                            "반납예측건수": "예상 반납량(대)",
                            "종료재고": "예상 종료 재고(대)",
                            "하한재고": "적정 하한(대)",
                            "상한재고": "적정 상한(대)",
                            "부족량": "예상 부족량(대)",
                            "초과량": "예상 초과량(대)",
                        }).copy()
                        future_detail["시간"] = future_detail["시간"].map(
                            lambda value: f"{int(value):02d}:00"
                        )
                        st.dataframe(
                            future_detail,
                            hide_index=True,
                            use_container_width=True,
                        )
                else:
                    st.caption("선택 정류소의 근접 미래 예측 데이터가 없습니다.")

            if st.button(
                "운영 현황에서 상세 보기",
                key=f"open_station_detail_{selected_station_id}",
                use_container_width=True,
            ):
                go_to_operations(station_id=selected_station_id)

    else:
        st.subheader("우선 확인 정류소")
        map_shortage_data = shortage_data[
            shortage_data["대여소_ID"].astype(str).isin(valid_map_station_ids)
        ].copy()
        map_surplus_data = surplus_data[
            surplus_data["대여소_ID"].astype(str).isin(valid_map_station_ids)
        ].copy()
        if map_shortage_data.empty and map_surplus_data.empty:
            st.info("지도 좌표가 확인된 우선 확인 정류소가 없습니다.")
        else:
            shortage_view = map_shortage_data.copy()
            shortage_view["우선순위"] = range(1, len(shortage_view) + 1)
            shortage_view["불균형량"] = shortage_view["부족량"]
            shortage_view["확인 사유"] = "공급부족"

            surplus_view = map_surplus_data.copy()
            surplus_view["우선순위"] = range(1, len(surplus_view) + 1)
            surplus_view["불균형량"] = surplus_view["초과량"]
            surplus_view["확인 사유"] = "공급과잉"

            priority_view = pd.concat(
                [shortage_view, surplus_view], ignore_index=True
            ).rename(columns={
                "대여소_ID": "정류소 ID",
                "종료재고": "현재 추정 재고(대)",
                "하한재고": "적정 하한(대)",
                "상한재고": "적정 상한(대)",
                "불균형량": "부족·초과량(대)",
            })
            priority_columns = [
                "우선순위", "정류소 ID", "확인 사유", "현재 추정 재고(대)",
                "적정 하한(대)", "적정 상한(대)", "부족·초과량(대)",
            ]
            priority_rows = []
            for row in priority_view[priority_columns].to_dict("records"):
                priority_rows.append({
                    "rank": int(row["우선순위"]),
                    "id": str(row["정류소 ID"]),
                    "reason": str(row["확인 사유"]),
                    "stock": None if pd.isna(row["현재 추정 재고(대)"]) else float(row["현재 추정 재고(대)"]),
                    "lower": None if pd.isna(row["적정 하한(대)"]) else float(row["적정 하한(대)"]),
                    "upper": None if pd.isna(row["적정 상한(대)"]) else float(row["적정 상한(대)"]),
                    "imbalance": None if pd.isna(row["부족·초과량(대)"]) else float(row["부족·초과량(대)"]),
                })

            priority_result = render_priority_station_list(
                rows=priority_rows,
                hovered_station_id=st.session_state.get("dashboard_hovered_station_id"),
                selected_station_id=priority_selected_id,
                key=priority_component_key,
            )
            clicked_station_id = getattr(priority_result, "selected_station_id", None)
            if clicked_station_id:
                st.session_state["dashboard_selected_station_id"] = str(clicked_station_id)
                st.rerun()


# ==========================================
# 시간대별 수요 예측 비교 및 재고 안정성
# ==========================================

st.divider()
st.subheader("시간대별 실제 이용량과 모델 예측량 비교")
if rolling_result is None:
    st.warning("실적 및 예측 비교 데이터를 불러오지 못했습니다. 원천 OD, 초기 재고 이력, 예측 결과를 확인해 주세요.")
else:
    usage_fig = create_usage_comparison_figure(
        rolling_result["actual_hourly"], int(selected_hour)
    )
    st.plotly_chart(
        usage_fig,
        key="dashboard_usage_prediction_comparison",
        use_container_width=True,
        config={"displayModeBar": False, "scrollZoom": False},
    )


# ==========================================
# AI 운영 도우미 전달용 분석 맥락
# ==========================================

assistant_context = {
    "행정구": selected_district,
    "운영 기준일": selected_date.strftime("%Y-%m-%d"),
    "운영 기준 시각": f"{int(selected_hour):02d}:00",
    "최근 이용량 비교 범위": (
        rolling_result["summary"].get("실적비교구간", f"최근 {int(recent_window_hours)}시간")
        if rolling_result is not None else f"최근 {int(recent_window_hours)}시간"
    ),
    "KPI": {
        "공급부족 정류소 수": shortage_count,
        "적정 정류소 수": normal_count,
        "공급과잉 정류소 수": surplus_count,
        "적정 재고 범위 만족률(%)": round(satisfaction_rate, 1),
        "예상 미충족 대여 수요(대)": round(float(unmet_demand), 1),
    },
}

if rolling_result is not None:
    summary = rolling_result["summary"]
    assistant_context["최근 이용량 비교"] = {
        "대여 실적(대)": summary.get("최근대여실적"),
        "대여 예측(대)": round(float(summary.get("최근대여예측", 0)), 1),
        "대여 차이(실적-예측, 대)": round(float(summary.get("최근대여차이", 0)), 1),
        "반납 실적(대)": summary.get("최근반납실적"),
        "반납 예측(대)": round(float(summary.get("최근반납예측", 0)), 1),
        "반납 차이(실적-예측, 대)": round(float(summary.get("최근반납차이", 0)), 1),
    }

    assistant_context["현재 우선 확인 정류소"] = {
        "공급부족": shortage_data[["대여소_ID", "부족량", "종료재고", "하한재고"]].head(5).to_dict("records"),
        "공급과잉": surplus_data[["대여소_ID", "초과량", "종료재고", "상한재고"]].head(5).to_dict("records"),
    }

    future_context = rolling_result["hourly_result"].copy()
    near_future = future_context[
        future_context["기준_시간대"].lt(int(selected_hour) + 3)
    ] if not future_context.empty else future_context
    if not near_future.empty:
        assistant_context["근접 3시간 예측"] = {
            "예상 부족량 합계(대)": round(float(near_future["부족량"].sum()), 1),
            "예상 초과량 합계(대)": round(float(near_future["초과량"].sum()), 1),
            "부족 위험 정류소-시간 조합 수": int(near_future["운영상태"].eq("공급부족").sum()),
            "과잉 위험 정류소-시간 조합 수": int(near_future["운영상태"].eq("공급과잉").sum()),
        }

# 이전 자동 요약 생성 기능의 세션 상태를 정리하고 운영 데이터 맥락은 AI 운영 도우미에 전달한다.
for legacy_key in (
    "dashboard_briefing_signature",
    "dashboard_briefing_text",
    "dashboard_briefing_error",
    "dashboard_briefing_status",
):
    st.session_state.pop(legacy_key, None)

render_operations_assistant("dashboard", context=assistant_context)
