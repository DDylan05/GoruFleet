"""모든 페이지에서 동일한 위치와 크기로 제목을 표시하는 공통 모듈."""

from __future__ import annotations

import html

import streamlit as st


# 모든 페이지 제목에 공통으로 적용되는 값. 여기만 바꾸면 전 페이지가 함께 바뀐다.
TITLE_BOX_HEIGHT = "68px"
TITLE_FONT_SIZE = "2.5rem"


def render_page_title(title: str) -> None:
    """페이지 제목을 고정 높이 상자 안에 세로 가운데 정렬로 표시한다.

    페이지마다 제목 옆 조회 조건(행정구·날짜 등)의 배치가 달라도
    제목의 시작 위치와 글자 크기가 같도록 st.title 대신 사용한다.
    """
    st.markdown(
        f'<div style="height:{TITLE_BOX_HEIGHT}; display:flex; align-items:center; white-space:nowrap;">'
        f'<h1 style="font-size:{TITLE_FONT_SIZE}; line-height:1.2; font-weight:700; margin:0; padding:0;">'
        f"{html.escape(title)}</h1></div>",
        unsafe_allow_html=True,
    )
