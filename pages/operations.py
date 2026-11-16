import os

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from pathlib import Path
from html import escape as html_escape

from utils.daily_model_pipeline import DATA_DIR, STATION_PATH
from utils.page_header import render_page_title
from utils.operations_simulator import PREDICTION_DIR, build_history_path
from utils.rolling_operations import run_rolling_operations
from utils.kakao_station_map import render_kakao_station_map
from utils.suspect_usage import (
    get_suspect_candidates_signature,
    load_suspect_usage_hourly,
    get_suspect_raw_source_signature,
    load_suspect_usage_from_raw_od,
)


# ==========================================
# 1. 파일 경로 및 데이터 로딩
# ==========================================

BASE_DIR = Path(__file__).resolve().parents[1]

OUTPUT_DIR = BASE_DIR / "outputs"
OPERATIONS_DIR = OUTPUT_DIR / "operation_results"
LEGACY_OPERATIONS_PATH = OUTPUT_DIR / "operations_result.csv"


def get_available_operations_paths() -> dict:
    """날짜별 운영 결과 파일을 날짜 키로 반환한다. 구버전 단일 파일도 지원한다."""
    available_paths = {}
    for path in OPERATIONS_DIR.glob("operations_result_*.csv"):
        date_key = path.stem.replace("operations_result_", "")
        if len(date_key) == 8 and date_key.isdigit():
            try:
                available_paths[pd.to_datetime(date_key, format="%Y%m%d").date()] = path
            except ValueError:
                continue

    if available_paths:
        return dict(sorted(available_paths.items()))
    if LEGACY_OPERATIONS_PATH.is_file():
        return {"legacy": LEGACY_OPERATIONS_PATH}
    raise FileNotFoundError(f"운영 결과 파일을 찾을 수 없습니다: {OPERATIONS_DIR}")


@st.cache_data
def load_operations(file_path: str, file_mtime_ns: int, file_size: int):
    data = pd.read_csv(file_path, encoding="utf-8-sig")
    data.columns = data.columns.astype(str).str.strip()

    # 구버전 파일에서 날짜 컬럼명이 다른 경우 표준 컬럼명으로 통일한다.
    date_aliases = ["기준일", "기준일자", "예측일자", "운영일자", "날짜"]
    if "기준_날짜" not in data.columns:
        date_column = next((col for col in date_aliases if col in data.columns), None)
        if date_column:
            data = data.rename(columns={date_column: "기준_날짜"})
        else:
            # 날짜별 운영 결과 파일은 파일명에 YYYYMMDD가 있으므로 이를 사용한다.
            file_name = Path(file_path).stem
            date_token = file_name.replace("operations_result_", "")
            if len(date_token) == 8 and date_token.isdigit():
                file_date = pd.to_datetime(date_token, format="%Y%m%d")
                data["기준_날짜"] = file_date
            else:
                raise ValueError(
                    f"'{Path(file_path).name}' 파일에서 기준 날짜를 확인할 수 없습니다. "
                    f"현재 컬럼: {list(data.columns)}"
                )

    data["기준_날짜"] = pd.to_datetime(data["기준_날짜"], errors="coerce")
    if "대여소_ID" in data.columns:
        data["대여소_ID"] = data["대여소_ID"].astype(str).str.strip()
    return data


def resolve_actual_history_path(target_date: str) -> Path:
    """날짜별 재고 이력 파일을 찾고, 중복 다운로드로 붙은 (1) 파일명도 허용한다."""
    target_day = pd.to_datetime(target_date, format="%Y%m%d")
    canonical_path = build_history_path(target_day)
    if canonical_path.is_file():
        return canonical_path
    alternatives = list(canonical_path.parent.glob(f"{target_date}_gangseo_station_history*.csv"))
    if alternatives:
        return max(alternatives, key=lambda path: path.stat().st_mtime)
    return canonical_path


def get_actual_source_signature(
    target_date: str,
    operations_path: Path,
    history_path: Path,
) -> tuple:
    """실제 이용량 캐시를 갱신할 원천 파일의 수정 정보를 만든다."""
    target_day = pd.to_datetime(target_date, format="%Y%m%d")
    date_label = target_day.strftime("%Y%m%d")
    source_paths = [
        operations_path,
        DATA_DIR / f"tpss_bcycl_od_statnhm_{date_label}.csv",
        PREDICTION_DIR / f"demand_forecast_{date_label}.csv",
        history_path,
        STATION_PATH,
    ]
    signature = []
    for path in source_paths:
        if path.is_file():
            stat = path.stat()
            signature.append((str(path), stat.st_mtime_ns, stat.st_size))
        else:
            signature.append((str(path), None, None))
    return tuple(signature)


@st.cache_data(show_spinner=False)
def load_rolling_result_cached(
    target_date: str,
    reference_hour: int,
    recent_window_hours: int,
    operations_path: str,
    history_path: str,
    source_signature: tuple,
) -> dict:
    """실제 실적을 반영한 기준 시각 현재 상태와 하루 전체 실제·예측 데이터를 불러온다."""
    return run_rolling_operations(
        target_date=target_date,
        reference_hour=reference_hour,
        recent_window_hours=recent_window_hours,
        operations_path=operations_path,
        history_path=history_path,
    )


def add_datetime_axis(data: pd.DataFrame, target_date) -> pd.DataFrame:
    """정수 시간대를 선택 날짜의 datetime 값으로 변환한다."""
    result = data.copy()
    hours = pd.to_numeric(result["기준_시간대"], errors="coerce")
    result["시간"] = pd.Timestamp(target_date).normalize() + pd.to_timedelta(hours, unit="h")
    return result


def create_usage_comparison_figure(
    actual_hourly: pd.DataFrame,
    reference_hour: int,
):
    """대시보드와 동일한 방식으로 전체 또는 정류소별 실적·예측을 비교한다."""
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

    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.15,
        subplot_titles=("시간대별 대여량", "시간대별 반납량"),
    )

    figure.add_trace(go.Bar(
        x=hours, y=actual_rentals, name="실제 이용량",
        marker=dict(color="#E45756", opacity=0.72),
        legendgroup="actual", showlegend=True,
        hovertemplate="실제 대여: %{y:,.0f}대<extra></extra>",
    ), row=1, col=1)
    figure.add_trace(go.Scatter(
        x=hours, y=hourly["예측대여"], name="모델 예측량",
        mode="lines+markers", line=dict(color="#D97706", width=2.5, dash="dash"),
        marker=dict(size=5), legendgroup="prediction", showlegend=True,
        hovertemplate="모델 예측 대여량: %{y:,.1f}대<extra></extra>",
    ), row=1, col=1)
    figure.add_trace(go.Bar(
        x=hours, y=actual_returns, name="실제 이용량",
        marker=dict(color="#2563EB", opacity=0.72),
        legendgroup="actual", showlegend=False,
        hovertemplate="실제 반납: %{y:,.0f}대<extra></extra>",
    ), row=2, col=1)
    figure.add_trace(go.Scatter(
        x=hours, y=hourly["예측반납"], name="모델 예측량",
        mode="lines+markers", line=dict(color="#D97706", width=2.5, dash="dash"),
        marker=dict(size=5), legendgroup="prediction", showlegend=False,
        hovertemplate="모델 예측 반납량: %{y:,.1f}대<extra></extra>",
    ), row=2, col=1)

    reference_color = "#0F766E"
    for row in (1, 2):
        figure.add_vline(
            x=f"{int(reference_hour):02d}:00",
            line_dash="dot",
            line_color=reference_color,
            line_width=2,
            row=row,
            col=1,
        )

    figure.add_trace(go.Scatter(
        x=[None], y=[None], name="기준시각", mode="lines",
        line=dict(color=reference_color, width=2, dash="dot"),
        showlegend=True, hoverinfo="skip",
    ), row=1, col=1)

    tick_hours = [f"{hour:02d}:00" for hour in range(0, 24, 2)]
    figure.update_layout(
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
    figure.update_yaxes(title_text="대여량(대)", rangemode="tozero", row=1, col=1)
    figure.update_yaxes(title_text="반납량(대)", rangemode="tozero", row=2, col=1)
    for row in (1, 2):
        figure.update_xaxes(
            title_text="",
            type="category",
            tickmode="array",
            tickvals=tick_hours,
            ticktext=tick_hours,
            showticklabels=True,
            row=row,
            col=1,
        )
    return figure


def build_actual_status_counts(
    actual_hourly: pd.DataFrame,
    initial_inventory: pd.DataFrame,
    inventory_bounds: pd.DataFrame,
) -> pd.DataFrame:
    """0시 초기 재고와 강서구 내부 OD 실적을 누적해 실제 운영 상태를 집계한다."""
    required_actual = ["대여소_ID", "기준_시간대", "대여실적", "반납실적"]
    required_initial = ["대여소_ID", "초기재고"]
    required_bounds = ["대여소_ID", "기준_시간대", "하한재고", "상한재고"]

    for label, data, columns in [
        ("실제 OD 이용량", actual_hourly, required_actual),
        ("0시 초기 재고", initial_inventory, required_initial),
        ("시간대별 적정 재고 범위", inventory_bounds, required_bounds),
    ]:
        missing = [column for column in columns if column not in data.columns]
        if missing:
            raise ValueError(f"{label}에 필요한 컬럼이 없습니다: {missing}")

    actual = actual_hourly[required_actual].copy()
    actual["대여소_ID"] = actual["대여소_ID"].astype(str).str.strip()
    actual["기준_시간대"] = pd.to_numeric(actual["기준_시간대"], errors="coerce")
    actual = actual.dropna(subset=["대여소_ID", "기준_시간대"])
    actual["기준_시간대"] = actual["기준_시간대"].astype(int)

    if actual.duplicated(["대여소_ID", "기준_시간대"]).any():
        raise ValueError("실제 OD 이용량에 중복 정류소·시간대가 있습니다.")

    initial = initial_inventory[required_initial].copy()
    initial["대여소_ID"] = initial["대여소_ID"].astype(str).str.strip()
    initial["초기재고"] = pd.to_numeric(initial["초기재고"], errors="coerce")
    if initial["대여소_ID"].duplicated().any() or initial["초기재고"].isna().any():
        raise ValueError("0시 초기 재고에 중복 또는 누락 값이 있습니다.")

    bounds = inventory_bounds[required_bounds].copy()
    bounds["대여소_ID"] = bounds["대여소_ID"].astype(str).str.strip()
    bounds["기준_시간대"] = pd.to_numeric(bounds["기준_시간대"], errors="coerce")
    bounds["하한재고"] = pd.to_numeric(bounds["하한재고"], errors="coerce")
    bounds["상한재고"] = pd.to_numeric(bounds["상한재고"], errors="coerce")
    bounds = bounds.dropna(subset=["대여소_ID", "기준_시간대"])
    bounds["기준_시간대"] = bounds["기준_시간대"].astype(int)
    if bounds.duplicated(["대여소_ID", "기준_시간대"]).any():
        raise ValueError("시간대별 적정 재고 범위에 중복 정류소·시간대가 있습니다.")

    actual = actual.merge(initial, on="대여소_ID", how="left", validate="many_to_one")
    if actual["초기재고"].isna().any():
        missing_ids = actual.loc[actual["초기재고"].isna(), "대여소_ID"].drop_duplicates().tolist()
        raise ValueError(f"0시 초기 재고가 없는 정류소가 있습니다: {missing_ids[:10]}")

    actual = actual.merge(
        bounds,
        on=["대여소_ID", "기준_시간대"],
        how="left",
        validate="one_to_one",
    ).sort_values(["대여소_ID", "기준_시간대"]).reset_index(drop=True)

    for column in ["대여실적", "반납실적"]:
        actual[column] = pd.to_numeric(actual[column], errors="coerce").fillna(0)

    actual["시간대별_재고변화"] = actual["반납실적"] - actual["대여실적"]
    actual["실적기반_추정재고"] = (
        actual["초기재고"]
        + actual.groupby("대여소_ID")["시간대별_재고변화"].cumsum()
    )

    valid_bounds = actual["하한재고"].notna() & actual["상한재고"].notna()
    actual["실적운영상태"] = "범위 산출 불가"
    actual.loc[valid_bounds, "실적운영상태"] = "적정"
    actual.loc[
        valid_bounds & actual["실적기반_추정재고"].lt(actual["하한재고"]),
        "실적운영상태",
    ] = "공급부족"
    actual.loc[
        valid_bounds & actual["실적기반_추정재고"].gt(actual["상한재고"]),
        "실적운영상태",
    ] = "공급과잉"

    status_order = ["공급부족", "적정", "공급과잉"]
    counts = (
        actual[actual["실적운영상태"].isin(status_order)]
        .groupby(["기준_시간대", "실적운영상태"])["대여소_ID"]
        .nunique()
        .unstack(fill_value=0)
        .reindex(index=range(24), columns=status_order, fill_value=0)
    )
    counts.index.name = "기준_시간대"
    return counts


def create_status_comparison_figure(
    actual_counts: pd.DataFrame,
    prediction_counts: pd.DataFrame,
    reference_hour: int,
):
    """시간대별 실제·예측 운영 상태를 한 그래프의 누적 막대 쌍으로 비교한다."""
    status_colors = {
        "공급부족": "#E45756",
        "적정": "#447DB8",
        "공급과잉": "#E3A23B",
    }
    light_colors = {
        "공급부족": "#F78C8C",
        "적정": "#8FC5FC",
        "공급과잉": "#FAE190",
    }

    hours = list(range(24))
    hour_labels = [f"{hour:02d}:00" for hour in hours]
    observed = pd.Index(hours) < int(reference_hour)

    # 각 시간대에서 실제 막대는 왼쪽, 예측 막대는 오른쪽에 배치한다.
    actual_positions = [hour - 0.19 for hour in hours]
    prediction_positions = [hour + 0.19 for hour in hours]
    figure = go.Figure()

    for status_index, status in enumerate(STATUS_ORDER):
        color = status_colors[status]
        light_color = light_colors[status]
        actual_values = (
            actual_counts[status]
            .reindex(hours, fill_value=0)
            .astype(float)
            .where(observed)
        )
        predicted_values = (
            prediction_counts[status]
            .reindex(hours, fill_value=0)
            .astype(float)
        )

        figure.add_trace(go.Bar(
            x=actual_positions,
            y=actual_values.tolist(),
            width=0.34,
            name=f"실제 · {status}",
            legendgroup=f"{status}_actual",
            marker=dict(color=color),
            showlegend=False,
            customdata=[f"{label} {status}" for label in hour_labels],
            hovertemplate="%{customdata}<br>%{y:,.0f}개소<extra></extra>",
        ))
        figure.add_trace(go.Bar(
            x=prediction_positions,
            y=predicted_values.tolist(),
            width=0.34,
            name=f"예측 · {status}",
            legendgroup=f"{status}_prediction",
            marker=dict(
                color=light_color,
                line=dict(color=color, width=0.6),
            ),
            showlegend=False,
            customdata=[f"{label}  {status}" for label in hour_labels],
            hovertemplate="%{customdata}<br>%{y:,.0f}개소<extra></extra>",
        ))

    cutoff_x = int(reference_hour) - 0.5
    if int(reference_hour) < 24:
        figure.add_vrect(
            x0=cutoff_x,
            x1=23.6,
            fillcolor="rgba(100, 116, 139, 0.07)",
            line_width=0,
            layer="below",
        )
    # 이용량 비교 그래프와 동일한 초록색 점선으로 기준 시각을 표시한다.
    figure.add_vline(
        x=cutoff_x,
        line_width=2,
        line_dash="dot",
        line_color="#0F766E",
    )

    tick_hours = hours[::2]
    figure.update_layout(
        height=440,
        margin=dict(l=10, r=12, t=30, b=12),
        barmode="stack",
        bargap=0.12,
        hovermode="x unified",
        showlegend=False,
        xaxis=dict(
            title_text="시간대",
            type="linear",
            range=[-0.6, 23.6],
            tickmode="array",
            tickvals=tick_hours,
            ticktext=[hour_labels[hour] for hour in tick_hours],
            showgrid=False,
        ),
        yaxis=dict(
            title_text="정류소 수(개소)",
            range=[0, 188],
            dtick=20,
            rangemode="tozero",
        ),
    )

    # 상태별 사용자 정의 범례
    legend_items = [
        ("공급부족", status_colors["공급부족"], light_colors["공급부족"], 0.77),
        ("적정", status_colors["적정"], light_colors["적정"], 0.85),
        ("공급과잉", status_colors["공급과잉"], light_colors["공급과잉"], 0.92),
    ]

    for status, actual_color, prediction_color, x_start in legend_items:
        # 실제 색상
        figure.add_shape(
            type="rect",
            xref="paper",
            yref="paper",
            x0=x_start,
            x1=x_start + 0.014,
            y0=1.045,
            y1=1.085,
            fillcolor=actual_color,
            line=dict(color=actual_color, width=0.5),
        )

        # 예측 색상
        figure.add_shape(
            type="rect",
            xref="paper",
            yref="paper",
            x0=x_start + 0.018,
            x1=x_start + 0.032,
            y0=1.045,
            y1=1.085,
            fillcolor=prediction_color,
            line=dict(color=actual_color, width=0.6),
        )

        # 상태명
        figure.add_annotation(
            x=x_start + 0.038,
            y=1.065,
            xref="paper",
            yref="paper",
            text=status,
            showarrow=False,
            xanchor="left",
            yanchor="middle",
            font=dict(size=11),
        )

    return figure
@st.cache_data(show_spinner=False)
def load_station_coordinates(
    station_path: str,
    file_mtime_ns: int,
    file_size: int,
) -> pd.DataFrame:
    """정류소 마스터에서 지도 표시용 좌표만 불러온다."""
    stations = pd.read_csv(station_path, encoding="cp949")
    stations.columns = stations.columns.astype(str).str.strip()
    required = ["대여소_ID", "위도", "경도"]
    missing = [column for column in required if column not in stations.columns]
    if missing:
        raise ValueError(f"정류소 마스터에 지도 필수 컬럼이 없습니다: {missing}")

    stations = stations[required].copy()
    stations["대여소_ID"] = stations["대여소_ID"].astype(str).str.strip()
    stations["위도"] = pd.to_numeric(stations["위도"], errors="coerce")
    stations["경도"] = pd.to_numeric(stations["경도"], errors="coerce")
    return stations.dropna(subset=["위도", "경도"]).drop_duplicates("대여소_ID")


def get_kakao_map_js_key() -> str:
    """환경변수 또는 Streamlit secrets에서 카카오 지도 JavaScript 키를 읽는다."""
    key = os.getenv("KAKAO_MAP_JAVASCRIPT_KEY", "").strip()
    if key:
        return key
    try:
        return str(st.secrets["KAKAO_MAP_JAVASCRIPT_KEY"]).strip()
    except Exception:
        return ""


def to_map_number(value):
    """지도 툴팁에 전달할 숫자를 float 또는 None으로 변환한다."""
    numeric_value = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return None if pd.isna(numeric_value) else float(numeric_value)


def build_operations_marker_data(map_data: pd.DataFrame) -> list[dict]:
    """현재 운영 현황 필터가 적용된 데이터를 카카오 지도 마커 형식으로 변환한다."""
    stock_column = "현재추정재고" if "현재추정재고" in map_data.columns else "종료재고"
    lower_column = "현재하한재고" if "현재하한재고" in map_data.columns else "하한재고"
    upper_column = "현재상한재고" if "현재상한재고" in map_data.columns else "상한재고"
    shortage_column = "현재부족량" if "현재부족량" in map_data.columns else "부족량"
    surplus_column = "현재초과량" if "현재초과량" in map_data.columns else "초과량"

    marker_data = []
    for record in map_data.to_dict("records"):
        marker_data.append({
            "id": str(record["대여소_ID"]),
            "lat": float(record["위도"]),
            "lng": float(record["경도"]),
            "status": str(record.get("운영상태", "범위 산출 불가")),
            "stock": to_map_number(record.get(stock_column)),
            "lower": to_map_number(record.get(lower_column)),
            "upper": to_map_number(record.get(upper_column)),
            "shortage": to_map_number(record.get(shortage_column)),
            "surplus": to_map_number(record.get(surplus_column)),
        })
    return marker_data


def format_time_axis(figure) -> None:
    """Plotly의 시간축을 HH:MM으로 표시한다."""
    figure.update_xaxes(tickformat="%H:%M", hoverformat="%H:%M", dtick=7200000)



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

def sync_operations_recent_window() -> None:
    """운영 현황에서 선택한 비교 구간을 대시보드와 공유한다."""
    st.session_state["operations_analysis_recent_window_hours"] = int(
        st.session_state["operations_recent_window_widget"]
    )


try:
    operations_paths = get_available_operations_paths()
    if "legacy" in operations_paths:
        operations_path = operations_paths["legacy"]
        operations_stat = operations_path.stat()
        operations = load_operations(
            str(operations_path), operations_stat.st_mtime_ns, operations_stat.st_size
        )
        legacy_dates = sorted(operations["기준_날짜"].dropna().dt.date.unique())
        if not legacy_dates:
            raise ValueError("운영 결과 파일에 유효한 기준 날짜가 없습니다.")
        date_options = legacy_dates
        st.caption(f"구버전 통합 운영 결과 · {len(date_options):,}개 날짜")
    else:
        operations_frames = []
        for file_date, file_path in operations_paths.items():
            file_stat = file_path.stat()
            operations_frames.append(load_operations(
                str(file_path), file_stat.st_mtime_ns, file_stat.st_size
            ))
        operations = pd.concat(operations_frames, ignore_index=True)
        date_options = sorted(operations["기준_날짜"].dropna().dt.date.unique())
        if not date_options:
            raise ValueError("날짜별 운영 결과 파일에 유효한 기준 날짜가 없습니다.")

except FileNotFoundError as error:
    st.error(str(error))
    st.stop()

except Exception as e:
    st.error(f"데이터 로딩 오류: {e}")
    st.stop()


# ==========================================
# 2. 공통 데이터 준비
# ==========================================

# 운영 결과 CSV의 구버전·신버전 미충족 수요 컬럼명을 통일한다.
if "미충족대여수요" not in operations.columns and "예상_미충족대여수요" in operations.columns:
    operations["미충족대여수요"] = operations["예상_미충족대여수요"]

# 실제 대여 실적은 OD 연동으로 보완하므로 운영 결과 CSV에 없어도 페이지를 열 수 있게 한다.
if "실제대여량" not in operations.columns:
    operations["실제대여량"] = pd.NA

required_columns = [
    "기준_날짜", "기준_시간대", "대여소_ID", "대여예측건수", "반납예측건수",
    "대여수요_정수", "반납량_정수", "시작재고", "종료재고",
    "하한재고", "상한재고", "범위상태", "부족량", "초과량", "운영상태",
    "부족우선순위", "초과우선순위", "미충족대여수요"
]

missing_columns = [col for col in required_columns if col not in operations.columns]

if missing_columns:
    st.error(f"필수 데이터 컬럼이 없습니다: {missing_columns}")
    st.stop()


operations = operations.dropna(subset=["기준_날짜"]).copy()
operations["기준_시간대"] = pd.to_numeric(operations["기준_시간대"], errors="coerce")
operations["운영상태"] = (operations["운영상태"].fillna("범위 산출 불가"))

numeric_columns = [
    "대여예측건수", "반납예측건수", "대여수요_정수", "반납량_정수",
    "실제대여량", "시작재고", "종료재고", "하한재고", "상한재고",
    "부족량", "초과량", "부족우선순위", "초과우선순위", "미충족대여수요"
]

for col in numeric_columns:
    operations[col] = pd.to_numeric(operations[col], errors="coerce")

if not date_options:
    st.error("분석할 날짜가 없습니다.")
    st.stop()


# ==========================================
# 3. 드릴다운 요청값 전달받기
# ==========================================

tab_options = ["전체 운영 추이", "정류소별 운영 현황", "정류소별 상세 분석"]
tab_aliases = {
    "전체 추이": "전체 운영 추이",
    "정류소 목록": "정류소별 운영 현황",
    "정류소 상세": "정류소별 상세 분석",
}

tab_request = st.session_state.pop("ops_tab_request", None)
tab_request = tab_aliases.get(tab_request, tab_request)
if tab_request in tab_options:
    st.session_state["operations_tabs"] = tab_request

current_tab = st.session_state.get("operations_tabs")
if current_tab in tab_aliases:
    st.session_state["operations_tabs"] = tab_aliases[current_tab]

filter_options = ["전체","공급부족","적정","공급과잉","범위 산출 불가"]
filter_request = st.session_state.pop("ops_filter_request", None)

if filter_request in filter_options:
    st.session_state["ops_filter_widget"] = filter_request

station_options = sorted(operations["대여소_ID"].dropna().unique().tolist())

# 지도 선택 상태와 외부(표/selectbox) 선택 요청을 구분하기 위한 버전값
st.session_state.setdefault("ops_map_selection_revision", 0)
station_request = st.session_state.pop("ops_station_request", None)

if station_request in station_options:
    st.session_state["selected_station_id"] = station_request
    st.session_state["detail_station_widget"] = station_request

# 표 또는 지도에서 선택한 정류소는 selectbox가 생성되기 전에 반영한다.
table_station_request = st.session_state.pop("ops_table_station_request", None)
if table_station_request in station_options:
    st.session_state["selected_station_id"] = table_station_request
    st.session_state["ops_station_select"] = table_station_request
    st.session_state["detail_station_widget"] = table_station_request
    st.session_state["ops_map_selected_station_id"] = table_station_request
    st.session_state["ops_map_selection_revision"] += 1

map_station_request = st.session_state.pop("ops_map_station_request", None)
if map_station_request in station_options:
    st.session_state["selected_station_id"] = map_station_request
    st.session_state["ops_station_select"] = map_station_request
    st.session_state["detail_station_widget"] = map_station_request
    st.session_state["ops_map_selected_station_id"] = map_station_request
    st.session_state["ops_map_selection_revision"] += 1

# ==========================================
# 4. 날짜 및 시간 선택
# ==========================================

latest_date = date_options[-1]
requested_date = st.session_state.pop("ops_date_request", None)
selected_date = requested_date if requested_date in date_options else latest_date

if requested_date in date_options:
    st.session_state["ops_date_widget"] = requested_date
elif st.session_state.get("ops_date_widget") not in date_options:
    st.session_state["ops_date_widget"] = selected_date

SEOUL_DISTRICTS = [
    "종로구", "중구", "용산구", "성동구", "광진구",
    "동대문구", "중랑구", "성북구", "강북구", "도봉구",
    "노원구", "은평구", "서대문구", "마포구", "양천구",
    "강서구", "구로구", "금천구", "영등포구", "동작구",
    "관악구", "서초구", "강남구", "송파구", "강동구",
]

title_col, district_col, date_col, hour_col = st.columns([2.5, 1.0, 1.0, 1.0])
with title_col:
    render_page_title("정류소별 운영 분석")

with district_col:
    st.markdown("**행정구 선택**")
    selected_district = st.selectbox(
        "행정구",
        options=SEOUL_DISTRICTS,
        index=SEOUL_DISTRICTS.index("강서구"),
        key="district_select",
        label_visibility="collapsed",
    )

with date_col:
    st.markdown("**기준 날짜**")
    selected_date = st.selectbox(
        "분석 기준일", date_options, key="ops_date_widget",
        format_func=lambda x: x.strftime("%Y-%m-%d"), label_visibility="collapsed"
    )

if selected_district != "강서구":
    st.info(f"{selected_district} 데이터는 현재 준비 중입니다.")
    st.stop()

day_data = operations[operations["기준_날짜"].dt.date == selected_date].copy()
if "legacy" in operations_paths:
    selected_operations_path = operations_paths["legacy"]
else:
    selected_operations_path = operations_paths.get(selected_date)
    if selected_operations_path is None:
        st.error(f"선택한 날짜({selected_date})의 운영 결과 파일을 찾을 수 없습니다.")
        st.stop()

hour_options = sorted(day_data["기준_시간대"].dropna().unique().tolist())
if not hour_options:
    st.warning("선택한 날짜의 시간대 데이터가 없습니다.")
    st.stop()

requested_hour = st.session_state.pop("ops_hour_request", None)
if requested_hour in hour_options:
    st.session_state["ops_hour_widget"] = requested_hour
elif st.session_state.get("ops_hour_widget") not in hour_options:
    st.session_state["ops_hour_widget"] = 9 if 9 in hour_options else hour_options[0]

with hour_col:
    st.markdown("**기준 시간**")
    selected_hour = st.selectbox(
        "분석 시간대", hour_options, key="ops_hour_widget",
        format_func=lambda x: f"{int(x):02d}:00", label_visibility="collapsed"
    )

# 실제 대여·반납은 원천 OD를 기준으로 집계한다. 실패하면 기존 운영 결과의
# 실제 대여량은 유지하고, 실제 반납량은 자료 없음으로 표시한다.
rolling_result = None
actual_hourly = None
actual_load_error = None
target_date_label = selected_date.strftime("%Y%m%d")
shared_recent_window_key = "operations_analysis_recent_window_hours"
st.session_state.setdefault(shared_recent_window_key, 3)
if st.session_state[shared_recent_window_key] not in (2, 3):
    st.session_state[shared_recent_window_key] = 3
recent_window_hours = int(st.session_state[shared_recent_window_key])
try:
    actual_history_path = resolve_actual_history_path(target_date_label)
    actual_signature = get_actual_source_signature(
        target_date_label, selected_operations_path, actual_history_path
    )
    with st.spinner("실제 실적을 반영한 기준 시각 운영 상태를 불러오는 중입니다..."):
        rolling_result = load_rolling_result_cached(
            target_date_label,
            int(selected_hour),
            int(recent_window_hours),
            str(selected_operations_path),
            str(actual_history_path),
            actual_signature,
        )
    actual_hourly = rolling_result["actual_hourly"]
except Exception as error:
    actual_load_error = f"{type(error).__name__}: {error}"

if actual_hourly is not None and not actual_hourly.empty:
    actual_display = actual_hourly[[
        "대여소_ID", "기준_시간대", "대여실적", "반납실적"
    ]].rename(columns={
        "대여실적": "실제대여량",
        "반납실적": "실제반납량",
    })
    day_data = day_data.drop(columns=["실제대여량", "실제반납량"], errors="ignore").merge(
        actual_display,
        on=["대여소_ID", "기준_시간대"],
        how="left",
        validate="one_to_one",
    )
else:
    if "실제대여량" not in day_data.columns:
        day_data["실제대여량"] = pd.NA
    day_data["실제반납량"] = pd.NA

day_data["실제대여량"] = pd.to_numeric(day_data["실제대여량"], errors="coerce")
day_data["실제반납량"] = pd.to_numeric(day_data["실제반납량"], errors="coerce")

# 고장 의심은 후보 CSV 생성 여부에 의존하지 않고 선택 날짜의 원본 OD에서 직접 집계한다.
# 운영 현황·대시보드는 기존 기준인 출발시간 / 시작 정류소를 사용한다.
# 원본 OD가 없거나 분석에 실패한 경우에만 기존 후보 CSV를 보조 자료로 조회한다.
suspect_raw_signature = get_suspect_raw_source_signature(
    target_date_label, DATA_DIR, STATION_PATH
)
suspect_hourly, suspect_data_available, suspect_source_name = load_suspect_usage_from_raw_od(
    target_date_label,
    suspect_raw_signature,
    DATA_DIR,
    STATION_PATH,
    aggregation_basis="출발시간",
)

if not suspect_data_available:
    suspect_signature = get_suspect_candidates_signature(OUTPUT_DIR)
    suspect_hourly, suspect_data_available, suspect_source_name = load_suspect_usage_hourly(
        target_date_label,
        suspect_signature,
    )
if suspect_data_available:
    day_data = day_data.merge(
        suspect_hourly,
        on=["대여소_ID", "기준_시간대"],
        how="left",
        validate="one_to_one",
    )
    day_data["점검의심이용건수"] = (
        pd.to_numeric(day_data["점검의심이용건수"], errors="coerce")
        .fillna(0)
        .astype(int)
    )
else:
    day_data["점검의심이용건수"] = pd.NA

hour_data = day_data[day_data["기준_시간대"] == selected_hour].copy()

# 선택 날짜·기준 시각의 운영 KPI 계산값을 준비한다. 화면 출력은 전체 추이 탭에서만 한다.
kpi_source = (
    rolling_result["station_result"].copy()
    if rolling_result is not None
    else hour_data.copy()
)
shortage_count = int(kpi_source.loc[kpi_source["운영상태"].eq("공급부족"), "대여소_ID"].nunique())
normal_count = int(kpi_source.loc[kpi_source["운영상태"].eq("적정"), "대여소_ID"].nunique())
surplus_count = int(kpi_source.loc[kpi_source["운영상태"].eq("공급과잉"), "대여소_ID"].nunique())
valid_bounds = kpi_source["하한재고"].notna() & kpi_source["상한재고"].notna()
valid_station_count = int(kpi_source.loc[valid_bounds, "대여소_ID"].nunique())
satisfaction = normal_count / valid_station_count * 100 if valid_station_count else 0
if rolling_result is not None:
    future_result = rolling_result["hourly_result"]
    unmet_demand = (
        pd.to_numeric(future_result["예상_미충족대여수요"], errors="coerce").fillna(0).sum()
        if not future_result.empty else 0
    )
else:
    unmet_demand = pd.to_numeric(hour_data["미충족대여수요"], errors="coerce").fillna(0).sum()

suspect_hour_total = (
    pd.to_numeric(hour_data["점검의심이용건수"], errors="coerce").sum(min_count=1)
    if "점검의심이용건수" in hour_data.columns else pd.NA
)
suspect_hour_total_label = (
    f"{float(suspect_hour_total):,.0f}건"
    if pd.notna(suspect_hour_total) else "0건"
)

# 현재 운영 KPI는 전체 운영 추이 탭 상단에서만 출력한다.


# ==========================================
# 5. 하위 탭 구성
# ==========================================

tab_overview, tab_list, tab_detail = st.tabs(tab_options, key="operations_tabs", on_change="rerun")

# 공통 그래프 색상
DEMAND_COLORS = {"예상 대여": "#E45756", "예상 반납": "#2878A5"}
STATUS_ORDER = ["공급부족", "적정", "공급과잉"]
STATUS_COLORS = {"공급부족": "#DC5050", "적정": "#447DB8", "공급과잉": "#C99B22"}

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
# 6. 하위 탭 A - 전체 운영 추이
# ==========================================

if tab_overview.open:
    with tab_overview:
        st.subheader("현재 운영 현황")
        kpi_columns = st.columns(6, gap="small")
        with kpi_columns[0]:
            show_status_kpi_card("공급부족 정류소", f"{shortage_count:,}개소", KPI_ACCENT_COLORS[0])
        with kpi_columns[1]:
            show_status_kpi_card("적정 정류소", f"{normal_count:,}개소", KPI_ACCENT_COLORS[1])
        with kpi_columns[2]:
            show_status_kpi_card("공급과잉 정류소", f"{surplus_count:,}개소", KPI_ACCENT_COLORS[2])
        with kpi_columns[3]:
            show_status_kpi_card(
                "미충족 대여 수요",
                f"{unmet_demand:,.0f}대",
                KPI_ACCENT_COLORS[3],
                help_text=(
                    "기준 시각 이후 남은 시간대에 재고 부족으로 충족하지 못할 것으로 예상되는 대여 수요의 합계입니다."
                    if rolling_result is not None else "실적 분석을 불러오지 못해 선택한 시간대의 일일 시뮬레이션 미충족 수요를 표시합니다."
                ),
            )
        with kpi_columns[4]:
            show_status_kpi_card(
                "적정 재고 범위 만족률",
                f"{satisfaction:.1f}%",
                KPI_ACCENT_COLORS[4],
                help_text=(
                    "적정 재고 범위가 산출된 정류소 중, 기준 시각의 실적 기반 추정 재고가 하한·상한 사이에 있는 정류소의 비율입니다."
                    if rolling_result is not None else "적정 재고 범위가 산출된 정류소 중, 선택 시간대의 일일 예측 종료재고가 범위 안에 있는 비율입니다."
                ),
            )
        with kpi_columns[5]:
            show_status_kpi_card("고장 의심", suspect_hour_total_label, KPI_ACCENT_COLORS[5])
        if actual_load_error:
            st.warning(
                "실제 이용량 원천 데이터를 불러오지 못했습니다. 실제 대여량은 운영 결과 파일에 저장된 값이 있으면 표시하고, 실제 반납량은 자료 없음으로 표시합니다."
            )
            with st.expander("실제 이용량 연결 상세 사유"):
                st.caption(actual_load_error)

        st.divider()
        st.subheader("시간대별 전체 운영 변화")

        status_counts = (
            day_data[day_data["운영상태"].isin(STATUS_ORDER)]
            .groupby(["기준_시간대", "운영상태"])["대여소_ID"]
            .nunique()
            .unstack(fill_value=0)
            .reindex(index=hour_options, columns=STATUS_ORDER, fill_value=0)
        )

        usage_summary = (
            day_data.groupby("기준_시간대")
            .agg(
                실제대여량=("실제대여량", lambda values: values.sum(min_count=1)),
                대여예측건수=("대여예측건수", "sum"),
                실제반납량=("실제반납량", lambda values: values.sum(min_count=1)),
                반납예측건수=("반납예측건수", "sum"),
                미충족대여수요=("미충족대여수요", "sum"),
                점검의심이용건수=("점검의심이용건수", lambda values: values.sum(min_count=1)),
            )
            .reindex(hour_options)
        )

        trend_summary = usage_summary.join(status_counts).fillna({
            "공급부족": 0, "적정": 0, "공급과잉": 0, "미충족대여수요": 0,
        })
        trend_summary.index.name = "기준_시간대"


        # 대시보드와 동일한 전체 정류소 실제 이용량·모델 예측 비교
        st.markdown("##### 시간대별 실제 이용량과 모델 예측량 비교")
        if actual_hourly is None or actual_hourly.empty:
            st.info("실제 OD 및 모델 예측 비교 데이터를 불러오지 못했습니다.")
        else:
            demand_figure = create_usage_comparison_figure(
                actual_hourly, int(selected_hour)
            )
            st.plotly_chart(
                demand_figure,
                key="operations_demand_trend",
                use_container_width=True,
                config={"displayModeBar": False},
            )

        # 실제 운영 상태와 예측 운영 상태를 각각 누적 막대그래프로 비교한다.
        st.markdown("##### 시간대별 실제·예측 정류소 운영 상태 비교")
        if rolling_result is None or actual_hourly is None or actual_hourly.empty:
            st.info("실제 OD 실적 또는 0시 초기 재고를 불러오지 못해 운영 상태를 비교할 수 없습니다.")
        else:
            try:
                initial_inventory = rolling_result["station_result"][
                    ["대여소_ID", "초기재고"]
                ].copy()
                inventory_bounds = day_data[[
                    "대여소_ID", "기준_시간대", "하한재고", "상한재고"
                ]].copy()
                actual_status_counts = build_actual_status_counts(
                    actual_hourly,
                    initial_inventory,
                    inventory_bounds,
                )
                status_figure = create_status_comparison_figure(
                    actual_status_counts,
                    status_counts,
                    int(selected_hour),
                )
                st.plotly_chart(
                    status_figure,
                    key="operations_status_actual_prediction_stacked",
                    use_container_width=True,
                    config={"displayModeBar": False},
                )
            except Exception as error:
                st.warning(f"실제·예측 운영 상태 비교 그래프를 계산하지 못했습니다: {error}")
        st.markdown("##### 시간대별 집계 데이터")
        summary_display = trend_summary.reset_index()
        summary_display["시간"] = summary_display["기준_시간대"].map(
            lambda value: (pd.Timestamp(selected_date) + pd.Timedelta(hours=int(value))).strftime("%H:%M")
        )
        summary_display = summary_display.rename(columns={
            "실제대여량": "실제 대여량(대)",
            "대여예측건수": "모델 예측 대여량(대)",
            "실제반납량": "실제 반납량(대)",
            "반납예측건수": "모델 예측 반납량(대)",
            "공급부족": "공급부족 정류소(개소)",
            "적정": "적정 정류소(개소)",
            "공급과잉": "공급과잉 정류소(개소)",
            "미충족대여수요": "미충족 대여 수요(대)",
            "점검의심이용건수": "고장 의심(건)",
        })
        summary_display = summary_display.drop(columns=["기준_시간대"])
        summary_display = summary_display[[
            "시간", "실제 대여량(대)", "모델 예측 대여량(대)", "실제 반납량(대)", "모델 예측 반납량(대)",
            "공급부족 정류소(개소)", "적정 정류소(개소)", "공급과잉 정류소(개소)",
            "미충족 대여 수요(대)", "고장 의심(건)"
        ]]
        for column in ["실제 대여량(대)", "실제 반납량(대)"]:
            summary_display[column] = summary_display[column].map(
                lambda value: f"{value:,.0f}" if pd.notna(value) else "자료 없음"
            )
        for column in ["모델 예측 대여량(대)", "모델 예측 반납량(대)", "미충족 대여 수요(대)"]:
            summary_display[column] = summary_display[column].map(
                lambda value: f"{value:,.1f}" if pd.notna(value) else "자료 없음"
            )
        summary_display["고장 의심(건)"] = summary_display["고장 의심(건)"].map(
            lambda value: f"{value:,.0f}" if pd.notna(value) else "자료 없음"
        )
        st.dataframe(summary_display, hide_index=True, use_container_width=True)


# ==========================================
# 7. 하위 탭 B - 정류소별 운영 현황
# ==========================================

if tab_list.open:
    with tab_list:
        # 표 선택 결과는 별도 요청값으로 저장하고, 다음 rerun 초기에 위젯 상태에 반영한다.
        def sync_selected_station():
            table_state = st.session_state.get("ops_station_table", {})
            selection_state = (
                table_state.get("selection", {})
                if isinstance(table_state, dict)
                else getattr(table_state, "selection", {})
            )
            selected_rows = (
                selection_state.get("rows", [])
                if isinstance(selection_state, dict)
                else getattr(selection_state, "rows", [])
            )
            displayed_station_rows = st.session_state.get("ops_station_table_rows", [])
            if selected_rows:
                row_index = int(selected_rows[0])
                if 0 <= row_index < len(displayed_station_rows):
                    clicked_station = str(displayed_station_rows[row_index])
                    if clicked_station in station_options:
                        st.session_state["ops_table_station_request"] = clicked_station

        # 상세 분석 선택지는 필터에 걸린 정류소가 아니라 전체 정류소를 기준으로 제공한다.
        if station_options and st.session_state.get("ops_station_select") not in station_options:
            default_station = st.session_state.get("selected_station_id")
            st.session_state["ops_station_select"] = (
                default_station if default_station in station_options else station_options[0]
            )

        header_columns = st.columns([2.2, 1.0, 1.1, 1.45, 1.05], vertical_alignment="bottom")
        with header_columns[0]:
            st.subheader("정류소별 운영 현황")
        with header_columns[1]:
            selected_status = st.selectbox(
                "운영 상태 필터", filter_options, key="ops_filter_widget"
            )
        with header_columns[2]:
            search_station = st.text_input(
                "정류소 ID 검색", placeholder="예: ST-1253", key="ops_station_search"
            )
        with header_columns[3]:
            if station_options:
                st.selectbox(
                    "상세 분석할 정류소", station_options, key="ops_station_select",
                    help="정류소별 운영 현황 표의 행이나 지도 마커를 선택하면 이 값이 자동으로 변경됩니다."
                )
            else:
                st.info("선택 가능한 정류소가 없습니다.")
        with header_columns[4]:
            def open_station_detail():
                selected_id = st.session_state.get("ops_station_select")
                if selected_id in station_options:
                    st.session_state["selected_station_id"] = selected_id
                    st.session_state["detail_station_widget"] = selected_id
                    st.session_state["operations_tabs"] = "정류소별 상세 분석"

            st.button(
                "상세 분석", on_click=open_station_detail,
                type="primary", use_container_width=True,
                disabled=not bool(station_options),
            )

        # widgets가 반영한 최신 필터 값을 기준으로 표를 구성한다.
        selected_status = st.session_state.get("ops_filter_widget", "전체")
        search_station = st.session_state.get("ops_station_search", "")
        list_data = hour_data.copy()
        if rolling_result is not None:
            current_list_columns = [
                "대여소_ID", "현재추정재고", "하한재고", "상한재고",
                "부족량", "초과량", "운영상태",
            ]
            current_list = rolling_result["station_result"][current_list_columns].rename(columns={
                "현재추정재고": "현재추정재고",
                "하한재고": "현재하한재고",
                "상한재고": "현재상한재고",
                "부족량": "현재부족량",
                "초과량": "현재초과량",
                "운영상태": "현재운영상태",
            })
            list_data = list_data.drop(
                columns=["운영상태", "하한재고", "상한재고", "부족량", "초과량", "부족우선순위", "초과우선순위"],
                errors="ignore",
            ).merge(current_list, on="대여소_ID", how="left", validate="one_to_one")
            list_data["운영상태"] = list_data["현재운영상태"]
            shortage_sort = "현재부족량"
            surplus_sort = "현재초과량"
        else:
            shortage_sort = "부족량"
            surplus_sort = "초과량"
        if selected_status != "전체":
            list_data = list_data[list_data["운영상태"] == selected_status]
        if search_station:
            list_data = list_data[list_data["대여소_ID"].str.contains(search_station, case=False, na=False)]
        list_data = list_data.sort_values(
            [shortage_sort, surplus_sort], ascending=[False, False], na_position="last"
        )

        if rolling_result is not None:
            display_columns = [
                "대여소_ID", "운영상태", "현재추정재고", "현재하한재고", "현재상한재고",
                "현재부족량", "현재초과량", "실제대여량", "대여예측건수", "실제반납량",
                "반납예측건수", "미충족대여수요", "점검의심이용건수", "종료재고",
            ]
        else:
            display_columns = [
                "대여소_ID", "운영상태", "범위상태", "시작재고", "종료재고",
                "실제대여량", "대여예측건수", "실제반납량", "반납예측건수",
                "미충족대여수요", "점검의심이용건수", "하한재고", "상한재고", "부족량", "부족우선순위", "초과량", "초과우선순위"
            ]
        list_display = list_data[display_columns].rename(columns={
            "대여소_ID": "정류소 ID", "운영상태": "운영 상태", "범위상태": "재고 범위 상태",
            "시작재고": "시작 재고(대)", "종료재고": "일일 예측 종료 재고(대)",
            "현재추정재고": "현재 추정 재고(대)", "현재하한재고": "현재 적정 하한(대)",
            "현재상한재고": "현재 적정 상한(대)", "현재부족량": "현재 부족량(대)",
            "현재초과량": "현재 초과량(대)", "실제대여량": "실제 대여량(대)",
            "대여예측건수": "모델 예측 대여량(대)", "실제반납량": "실제 반납량(대)",
            "반납예측건수": "모델 예측 반납량(대)", "미충족대여수요": "미충족 대여 수요(대)",
            "하한재고": "적정 하한(대)", "상한재고": "적정 상한(대)",
            "부족량": "부족량(대)", "부족우선순위": "부족 우선순위",
            "초과량": "초과량(대)", "초과우선순위": "초과 우선순위",
            "점검의심이용건수": "고장 의심(건)",
        })
        for flow_column in [
            "실제 대여량(대)", "모델 예측 대여량(대)", "실제 반납량(대)",
            "모델 예측 반납량(대)", "미충족 대여 수요(대)",
            "고장 의심(건)",
        ]:
            if flow_column in list_display.columns:
                list_display[flow_column] = list_display[flow_column].map(
                    lambda value: f"{value:,.0f}" if pd.notna(value) and (
                        flow_column.startswith("실제") or flow_column == "고장 의심(건)"
                    ) else f"{value:,.1f}" if pd.notna(value) else "자료 없음"
                )

        st.caption(f"조회 결과: {len(list_data):,}개 정류소")
        map_column, table_column = st.columns(2, gap="medium")

        with map_column:
            try:
                station_stat = STATION_PATH.stat()
                station_coordinates = load_station_coordinates(
                    str(STATION_PATH), station_stat.st_mtime_ns, station_stat.st_size
                )
                # 지도 좌표는 정류소 마스터의 원본 좌표를 기준으로 통일한다.
                map_source_data = list_data.drop(
                    columns=["위도", "경도"], errors="ignore"
                ).copy()
                map_data = map_source_data.merge(
                    station_coordinates[["대여소_ID", "위도", "경도"]],
                    on="대여소_ID",
                    how="left",
                    validate="many_to_one",
                )
                map_data["위도"] = pd.to_numeric(map_data["위도"], errors="coerce")
                map_data["경도"] = pd.to_numeric(map_data["경도"], errors="coerce")
                map_data = map_data.dropna(subset=["위도", "경도"]).copy()

                if map_data.empty:
                    st.info("현재 필터 조건에 해당하는 정류소의 지도 좌표가 없습니다.")
                else:
                    js_key = get_kakao_map_js_key()
                    if not js_key:
                        st.warning(
                            "카카오 지도 JavaScript 키가 없습니다. "
                            "KAKAO_MAP_JAVASCRIPT_KEY 환경변수 또는 .streamlit/secrets.toml을 확인해 주세요."
                        )
                    else:
                        visible_station_ids = set(map_data["대여소_ID"].astype(str))
                        selectbox_station_id = st.session_state.get("ops_station_select")
                        map_selected_station_id = (
                            selectbox_station_id
                            if selectbox_station_id in visible_station_ids
                            else None
                        )
                        st.session_state["ops_map_selected_station_id"] = map_selected_station_id
                        previous_map_input_id = st.session_state.get("ops_last_map_input_station_id")
                        parent_selection_changed = previous_map_input_id != map_selected_station_id
                        st.session_state["ops_last_map_input_station_id"] = map_selected_station_id

                        selected_map_station_id = render_kakao_station_map(
                            marker_data=build_operations_marker_data(map_data),
                            js_key=js_key,
                            selected_station_id=map_selected_station_id,
                            key="operations_kakao_station_map",
                            selection_revision=st.session_state.get("ops_map_selection_revision", 0),
                            dim_unselected_markers=True,
                        )
                        # 표·selectbox에서 선택값이 바뀐 직후에는 컴포넌트의 이전 반환값을 무시한다.
                        if (
                            not parent_selection_changed
                            and selected_map_station_id is not None
                            and selected_map_station_id in visible_station_ids
                            and selected_map_station_id != st.session_state.get("ops_station_select")
                        ):
                            st.session_state["ops_map_station_request"] = selected_map_station_id
                            st.rerun()
            except Exception as error:
                st.error(f"정류소 운영 지도 생성 오류: {error}")

        with table_column:
            st.session_state["ops_station_table_rows"] = list_data["대여소_ID"].astype(str).tolist()
            st.dataframe(
                list_display,
                hide_index=True,
                use_container_width=True,
                height=510,
                on_select=sync_selected_station,
                selection_mode="single-row",
                key="ops_station_table",
            )

# 8. 하위 탭 C - 정류소별 상세 분석
# ==========================================

if tab_detail.open:
    with tab_detail:
        if not station_options:
            st.subheader("정류소별 상세 분석")
            st.warning("분석 가능한 정류소가 없습니다.")
        else:
            current_station = st.session_state.get("selected_station_id")
            if current_station not in station_options:
                current_station = station_options[0]
            if st.session_state.get("detail_station_widget") not in station_options:
                st.session_state["detail_station_widget"] = current_station

            detail_header_columns = st.columns([4.4, 1.0, 1.0], vertical_alignment="center")
            operations_window_widget_key = "operations_recent_window_widget"
            st.session_state[operations_window_widget_key] = int(recent_window_hours)
            with detail_header_columns[0]:
                st.subheader("정류소별 상세 분석")
            with detail_header_columns[1]:
                selected_station = st.selectbox(
                    "분석 정류소", station_options, key="detail_station_widget",
                    label_visibility="collapsed",
                    help="상세 분석할 정류소를 변경합니다.",
                )
            with detail_header_columns[2]:
                st.selectbox(
                    "최근 이용량 비교 시간",
                    [2, 3],
                    format_func=lambda value: f"최근 {value}시간",
                    label_visibility="collapsed",
                    key=operations_window_widget_key,
                    on_change=sync_operations_recent_window,
                    help="실제 대여·반납량과 모델 예측량을 비교할 기간입니다.",
                )
            st.session_state["selected_station_id"] = selected_station
            station_data = day_data[day_data["대여소_ID"] == selected_station].sort_values("기준_시간대")

            if station_data.empty:
                st.warning("선택 정류소의 데이터가 없습니다.")
            else:
                latest_data = station_data[station_data["기준_시간대"] == selected_hour]
                latest = latest_data.iloc[0] if not latest_data.empty else station_data.iloc[-1]
                current_station_row = None
                if rolling_result is not None:
                    current_station_rows = rolling_result["station_result"][
                        rolling_result["station_result"]["대여소_ID"].astype(str).eq(str(selected_station))
                    ]
                    if not current_station_rows.empty:
                        current_station_row = current_station_rows.iloc[0]

                current_status = (
                    str(current_station_row.get("운영상태", "정보 없음"))
                    if current_station_row is not None
                    else str(latest.get("운영상태", "정보 없음"))
                )
                st.markdown(f"#### {selected_station} 상세 분석")
                if actual_load_error:
                    st.warning("실제 대여·반납 OD 자료를 연결하지 못했습니다. 실제 실적 기반 현재 재고 대신 일일 시뮬레이션 값을 표시합니다.")

                # 현재 상태와 재고 정보를 같은 순서로 표시한다.
                kpi_columns = st.columns(6, gap="small")
                if current_station_row is not None:
                    current_stock = current_station_row.get("현재추정재고")
                    current_lower = current_station_row.get("하한재고")
                    current_upper = current_station_row.get("상한재고")
                    if current_status == "공급부족":
                        imbalance_label = "현재 부족량"
                        imbalance_value = current_station_row.get("부족량", 0)
                    elif current_status == "공급과잉":
                        imbalance_label = "현재 초과량"
                        imbalance_value = current_station_row.get("초과량", 0)
                    elif current_status == "적정":
                        imbalance_label = "부족·초과량"
                        imbalance_value = 0
                    else:
                        imbalance_label = "부족·초과량"
                        imbalance_value = pd.NA

                    kpi_columns[0].metric("현재 상태", current_status)
                    kpi_columns[1].metric("현재 추정 재고", f"{float(current_stock):,.0f}대" if pd.notna(current_stock) else "자료 없음")
                    kpi_columns[2].metric("적정 하한", f"{float(current_lower):,.0f}대" if pd.notna(current_lower) else "자료 없음")
                    kpi_columns[3].metric("적정 상한", f"{float(current_upper):,.0f}대" if pd.notna(current_upper) else "자료 없음")
                    kpi_columns[4].metric(imbalance_label, f"{float(imbalance_value):,.0f}대" if pd.notna(imbalance_value) else "자료 없음")
                    suspect_count = latest.get("점검의심이용건수")
                    kpi_columns[5].metric(
                        "고장 의심",
                        f"{float(suspect_count):,.0f}건" if pd.notna(suspect_count) else "0건",
                    )
                else:
                    fallback_shortage = latest.get("부족량", 0)
                    fallback_surplus = latest.get("초과량", 0)
                    if current_status == "공급부족":
                        fallback_label, fallback_value = "예상 부족량", fallback_shortage
                    elif current_status == "공급과잉":
                        fallback_label, fallback_value = "예상 초과량", fallback_surplus
                    elif current_status == "적정":
                        fallback_label, fallback_value = "예상 부족·초과량", 0
                    else:
                        fallback_label, fallback_value = "예상 부족·초과량", pd.NA

                    kpi_columns[0].metric("현재 상태", current_status, help="실제 OD 실적 분석에 실패해 선택 시간대의 일일 시뮬레이션 상태를 표시합니다.")
                    kpi_columns[1].metric("현재 추정 재고", "실적 자료 없음", help="실제 대여·반납 실적을 연결하지 못해 현재 추정 재고를 계산할 수 없습니다.")
                    kpi_columns[2].metric("적정 하한", f"{float(latest['하한재고']):,.0f}대" if pd.notna(latest["하한재고"]) else "자료 없음")
                    kpi_columns[3].metric("적정 상한", f"{float(latest['상한재고']):,.0f}대" if pd.notna(latest["상한재고"]) else "자료 없음")
                    kpi_columns[4].metric(fallback_label, f"{float(fallback_value):,.0f}대" if pd.notna(fallback_value) else "자료 없음", help="실제 현재 부족·초과량이 아니라 일일 시뮬레이션 기준의 예상값입니다.")
                    suspect_count = latest.get("점검의심이용건수")
                    kpi_columns[5].metric(
                        "고장 의심",
                        f"{float(suspect_count):,.0f}건" if pd.notna(suspect_count) else "0건",
                    )

                # 대시보드와 동일한 비교 구간으로 실제 이용량과 모델 예측량을 비교한다.
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
                    '<span style="font-size:1.05rem; font-weight:650;">최근 이용량 비교</span>'
                    f'<span style="font-size:0.82rem; color:var(--st-secondary-text-color);">'
                    f'비교 범위: {html_escape(comparison_range_label)}</span></div>',
                    unsafe_allow_html=True,
                )

                if current_station_row is None or int(selected_hour) <= 0:
                    st.info("최근 이용량 비교 데이터를 확인할 수 없습니다.")
                else:
                    render_recent_usage_comparison(
                        current_station_row.get("최근_대여실적"),
                        current_station_row.get("최근_대여예측"),
                        current_station_row.get("최근_반납실적"),
                        current_station_row.get("최근_반납예측")
                    )

                # 시간대별 재고: 예상 종료재고와 적정 재고 범위
                st.markdown("##### 시간대별 재고 변화")
                stock_columns = ["기준_시간대", "종료재고", "하한재고", "상한재고"]
                stock_trend = add_datetime_axis(
                    station_data[stock_columns].sort_values("기준_시간대"), selected_date
                )
                fig_stock = go.Figure()

                valid_bounds = stock_trend["하한재고"].notna() & stock_trend["상한재고"].notna()
                if valid_bounds.any():
                    fig_stock.add_trace(go.Scatter(
                        x=stock_trend["시간"], y=stock_trend["하한재고"],
                        mode="lines", line=dict(width=0), showlegend=False,
                        hoverinfo="skip", connectgaps=False
                    ))
                    fig_stock.add_trace(go.Scatter(
                        x=stock_trend["시간"], y=stock_trend["상한재고"],
                        mode="lines", line=dict(width=0), fill="tonexty",
                        fillcolor="rgba(76, 175, 125, 0.20)",
                        name="적정 재고 범위", hoverinfo="skip", connectgaps=False
                    ))

                stock_hover = stock_trend[["하한재고", "상한재고"]].apply(
                    lambda row: [
                        f"{row['하한재고']:,.0f}" if pd.notna(row["하한재고"]) else "정보 없음",
                        f"{row['상한재고']:,.0f}" if pd.notna(row["상한재고"]) else "정보 없음"
                    ], axis=1
                ).tolist()
                fig_stock.add_trace(go.Scatter(
                    x=stock_trend["시간"], y=stock_trend["종료재고"],
                    mode="lines+markers", name="예상 종료 재고",
                    line=dict(color="#2878A5", width=3), marker=dict(size=7),
                    customdata=stock_hover,
                    hovertemplate=(
                        "%{x|%H:%M}<br>예상 종료 재고: %{y:,.1f}대<br>"
                        "적정 하한: %{customdata[0]}대<br>적정 상한: %{customdata[1]}대"
                        "<extra></extra>"
                    )
                ))
                fig_stock.update_layout(
                    height=360, margin=dict(l=10, r=10, t=20, b=10),
                    xaxis_title="시간", yaxis_title="자전거 수",
                    legend_title="", hovermode="x unified"
                )
                format_time_axis(fig_stock)
                if not valid_bounds.any():
                    st.info("해당 정류소는 적정 재고 범위 데이터가 없어 예상 종료 재고만 표시합니다.")
                st.plotly_chart(fig_stock, key="station_stock_trend", use_container_width=True)

                # 대시보드와 같은 그래프 형식으로 선택 정류소의 실제 이용량·모델 예측 비교
                st.markdown("##### 시간대별 실제 이용량과 모델 예측량 비교")
                if actual_hourly is None or actual_hourly.empty:
                    st.info("실제 OD 및 모델 예측 비교 데이터를 불러오지 못했습니다.")
                else:
                    station_actual_hourly = actual_hourly[
                        actual_hourly["대여소_ID"].astype(str).eq(str(selected_station))
                    ].copy()
                    if station_actual_hourly.empty:
                        st.info("선택한 정류소의 실제 이용량 및 예측 비교 데이터가 없습니다.")
                    else:
                        station_usage_figure = create_usage_comparison_figure(
                            station_actual_hourly, int(selected_hour)
                        )
                        st.plotly_chart(
                            station_usage_figure,
                            key="station_demand_trend",
                            use_container_width=True,
                            config={"displayModeBar": False},
                        )

                # 재고 범위 산출 근거
                with st.expander("재고 범위 산출 근거 확인"):
                    detail_columns = [
                        "기준_시간대", "시작재고", "종료재고", "IFI", "IFI_등급", "목표만족률(%)",
                        "관측일수", "분포기반하한", "분포기반상한", "하한재고", "상한재고",
                        "부족량", "초과량", "점검의심이용건수", "범위상태", "운영상태",
                    ]
                    available_detail_columns = [col for col in detail_columns if col in station_data.columns]
                    if available_detail_columns:
                        evidence_display = station_data[available_detail_columns].copy()
                        evidence_display["기준_시간대"] = evidence_display["기준_시간대"].map(
                            lambda value: (pd.Timestamp(selected_date) + pd.Timedelta(hours=int(value))).strftime("%H:%M")
                        )
                        evidence_display = evidence_display.rename(columns={
                            "기준_시간대": "시간",
                            "시작재고": "시작 재고(대)",
                            "종료재고": "예상 종료 재고(대)",
                            "IFI": "IFI(대)",
                            "IFI_등급": "IFI 등급",
                            "목표만족률(%)": "목표 만족률(%)",
                            "관측일수": "과거 관측일수(일)",
                            "분포기반하한": "분포 기반 하한(대)",
                            "분포기반상한": "분포 기반 상한(대)",
                            "하한재고": "적정 하한(대)",
                            "상한재고": "적정 상한(대)",
                            "부족량": "예상 부족량(대)",
                            "초과량": "예상 초과량(대)",
                            "점검의심이용건수": "고장 의심(건)",
                            "범위상태": "범위 산출 상태",
                            "운영상태": "운영 상태",
                        })
                        st.dataframe(
                            evidence_display,
                            hide_index=True, use_container_width=True
                        )
                    else:
                        st.info("재고 범위 산출 근거 컬럼이 없습니다.")
