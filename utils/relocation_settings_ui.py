from __future__ import annotations

import streamlit as st

from utils.relocation_settings import (
    load_relocation_settings,
    resolve_relocation_settings,
    save_relocation_settings,
)


def render_relocation_settings() -> None:
    """시스템 설정 페이지에 직접 재배치 설정 UI를 표시한다."""
    try:
        current_settings = load_relocation_settings()
    except Exception as error:
        st.error(f"저장된 재배치 설정을 읽지 못했습니다: {error}")
        return

    st.subheader("직접 재배치 운영 설정")
    st.caption(
        "정류소 간 최대 직선거리, 최소 이동 수량, 작업 묶음 수를 설정합니다. "
        "차량은 2대로 고정됩니다. 저장 후 직접 재배치 페이지에서 매칭을 다시 계산하세요."
    )

    with st.form("relocation_settings_form"):
        distance_col, quantity_col, bundle_col = st.columns(3)

        with distance_col:
            max_relocation_distance_km = st.number_input(
                "최대 이동 거리 (km)",
                min_value=0.1,
                max_value=20.0,
                value=float(current_settings["max_relocation_distance_km"]),
                step=0.1,
                format="%.1f",
                key="relocation_max_distance",
                help="공급 정류소와 수요 정류소 사이의 직선거리 기준입니다.",
            )

        with quantity_col:
            min_transfer_quantity = st.number_input(
                "최소 이동 수량 (대)",
                min_value=1,
                max_value=100,
                value=int(current_settings["min_transfer_quantity"]),
                step=1,
                key="relocation_min_quantity",
                help="이 값보다 적은 수량의 매칭 작업은 계획에서 제외합니다.",
            )

        with bundle_col:
            max_work_bundles = st.number_input(
                "최대 작업 묶음 수 (건)",
                min_value=1,
                max_value=500,
                value=int(current_settings["max_work_bundles"]),
                step=1,
                key="relocation_max_bundles",
                help="선택한 날짜·시간에 생성할 공급-수요 작업 묶음의 최대 개수입니다.",
            )

        st.caption("차량 대수: 2대 (고정)")
        submitted = st.form_submit_button(
            "재배치 설정 저장",
            type="primary",
            use_container_width=True,
        )

    if not submitted:
        return

    new_settings = {
        "max_relocation_distance_km": max_relocation_distance_km,
        "min_transfer_quantity": min_transfer_quantity,
        "max_work_bundles": max_work_bundles,
    }

    try:
        validated = resolve_relocation_settings(new_settings)
        save_relocation_settings(validated)
        st.session_state.pop("relocation_result", None)
        st.success(
            "재배치 설정을 저장했습니다. 직접 재배치 페이지에서 조건을 선택한 뒤 "
            "'공급·수요 매칭 계산'을 다시 실행하세요."
        )
    except Exception as error:
        st.error(f"재배치 설정 저장에 실패했습니다: {error}")
