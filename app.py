import streamlit as st

# =====================================
# 1. 공통 페이지 설정
# =====================================
st.set_page_config(
    page_title="GoruFleet",
    page_icon="🚲",
    layout="wide",
    initial_sidebar_state="expanded",
)

# =====================================
# 2. 페이지 정의
# =====================================
dashboard = st.Page(
    "pages/dashboard.py",
    title="운영 대시보드",
    icon="📈",
    default=True,
)

operations = st.Page(
    "pages/operations.py",
    title="정류소별 운영 분석",
    icon="📊",
)

promotion = st.Page(
    "pages/promotion.py",
    title="프로모션 시뮬레이션",
    icon="🎯",
)

relocation = st.Page(
    "pages/relocation.py",
    title="재배치 계획",
    icon="🚚",
)

ai_report = st.Page(
    "pages/ai_report.py",
    title="AI 운영 보고서",
    icon="🤖",
)

settings = st.Page(
    "pages/settings.py",
    title="운영 정책 설정",
    icon="⚙️",
)

# =====================================
# 3. 페이지 네비게이션
# =====================================
pg = st.navigation(
    [
        dashboard,
        operations,
        promotion,
        relocation,
        ai_report,
        settings,
    ],
    position="hidden",
)

# =====================================
# 4. 사이드바 공통 영역
# =====================================
st.sidebar.title("🚲 GoruFleet")
st.sidebar.caption("PM 재고 균형 운영 솔루션")

st.sidebar.divider()

# 메뉴 글씨 크기
st.markdown(
    """
    <style>
    /* 사이드바 맨 위 여백만 조정 */
    [data-testid="stSidebarHeader"] {
        height: 2.5rem !important;        /* 접기 버튼이 있는 줄의 높이 */
        min-height: 0 !important;
        margin-bottom: 0rem !important;
    }
    [data-testid="stSidebarUserContent"] {
        padding-top: 0rem !important;   /* 헤더 아래에서 제목까지의 여백 */
    }

    [data-testid="stSidebar"] a,
    [data-testid="stSidebar"] a p {
        font-size: 16px !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# =====================================
# 5. 사용자 정의 사이드바 메뉴
# =====================================
st.sidebar.page_link(
    dashboard,
    label="운영 대시보드",
    icon="📈",
)

st.sidebar.page_link(
    operations,
    label="정류소별 운영 분석",
    icon="📊",
)

st.sidebar.page_link(
    promotion,
    label="프로모션 시뮬레이션",
    icon="🎯",
)

st.sidebar.page_link(
    relocation,
    label="재배치 계획",
    icon="🚚",
)

st.sidebar.page_link(
    ai_report,
    label="AI 운영 보고서",
    icon="🤖",
)

st.sidebar.page_link(
    settings,
    label="운영 정책 설정",
    icon="⚙️",
)

# =====================================
# 6. 선택된 페이지 실행
# =====================================
pg.run()