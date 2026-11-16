import streamlit as st

from utils.operations_assistant import render_operations_assistant
from utils.page_header import render_page_title
from utils.promotion_settings_ui import render_promotion_settings
from utils.relocation_settings_ui import render_relocation_settings


# ==========================================
# 1. 페이지 제목
# ==========================================

render_page_title("운영 정책 설정")
render_operations_assistant("settings")

st.caption(
    "프로모션과 직접 재배치 시뮬레이션에 적용할 운영 정책과 제한 조건을 설정합니다."
)

st.divider()


# ==========================================
# 2. 프로모션 및 직접 재배치 운영 설정
# ==========================================

# 한 설정 영역에서 예상 못 한 오류가 나도 다른 영역과 나머지 페이지는 계속 표시한다.
try:
    render_promotion_settings()
except Exception as error:
    st.error(f"프로모션 운영 설정을 표시하지 못했습니다: {error}")

st.divider()

try:
    render_relocation_settings()
except Exception as error:
    st.error(f"직접 재배치 운영 설정을 표시하지 못했습니다: {error}")


# ==========================================
# 3. 데이터 캐시 초기화 (자주 쓰지 않는 관리 기능)
# ==========================================

st.divider()

with st.expander("데이터 새로고침"):
    st.caption(
        "화면에 이전 데이터가 남아 있는 것 같을 때만 사용하세요. 저장된 CSV 파일을 다시 읽도록 캐시를 초기화합니다."
    )

    if st.button("캐시 초기화", key="settings_clear_cache"):
        st.cache_data.clear()
        st.success("캐시를 초기화했습니다. 다음 화면 실행 시 파일을 다시 읽습니다.")
