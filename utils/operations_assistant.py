"""프로젝트 전 페이지에서 공통으로 사용하는 AI 운영 도우미."""

from __future__ import annotations

import inspect
import json
import os
import re
from typing import Any, Iterable

import streamlit as st

try:
    from openai import OpenAI
except ImportError:  # openai 패키지가 없을 때도 일반 페이지는 열리도록 한다.
    OpenAI = None


NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
DEFAULT_MODEL = "nvidia/nemotron-3-super-120b-a12b"
HISTORY_KEY = "operations_assistant_history"
PENDING_QUESTION_KEY = "operations_assistant_pending_question"
CONFIRM_CLEAR_KEY = "operations_assistant_confirm_clear"
MAX_HISTORY_MESSAGES = 24
MAX_CONTEXT_CHARS = 16000


PAGE_GUIDE = {
    "dashboard": {
        "title": "운영 대시보드",
        "purpose": "전체 운영 현황과 시간대별 실제 대여·반납 실적 및 저장된 모델 예측량의 차이를 확인하는 화면",
        "questions": [
            "시간대별 실제 대여·반납량과 모델 예측량은 어떻게 비교하나요?",
            "공급 부족과 공급 과잉은 어떤 차이가 있나요?",
            "운영 현황을 확인한 다음 어떤 업무를 진행하면 되나요?",
            "운영 기준 시각을 바꾸면 대시보드의 어떤 값이 달라지나요?",
        ],
    },
    "operations": {
        "title": "운영 현황",
        "purpose": "정류소별 예측 재고, 대여·반납 수요와 적정 재고 범위를 확인하는 화면",
        "questions": [
            "IFI가 무엇이고 어떻게 해석하나요?",
            "동적 재고 하한과 상한은 어떻게 활용하나요?",
            "예상 미충족 대여 수요는 무엇인가요?",
            "고장 의심 건수는 실제 고장 자전거 대수를 의미하나요?",
            "공급부족과 공급과잉 정류소는 어떤 순서로 확인하면 좋을까요?",
        ],
    },
    "promotion": {
        "title": "프로모션 의사결정",
        "purpose": "참여율과 마일리지 정책에 따른 이용자 자발적 이동 시나리오를 비교하는 화면",
        "questions": [
            "프로모션 참여율은 기대 이동량에 어떤 영향을 주나요?",
            "기대 이동량과 모의 부족량 감소율은 왜 다른 기준으로 계산되나요?",
            "기본 마일리지와 거리별 추가 보상은 어떻게 계산되나요?",
            "프로모션 결과를 실제 운영 성과로 봐도 되나요?",
            "참여율을 높이면 예상 마일리지 비용은 어떻게 달라지나요?",
        ],
    },
    "relocation": {
        "title": "직접 재배치",
        "purpose": "프로모션 적용 후 남은 공급·수요 불균형을 정류소 간 이동 작업으로 매칭하고 차량에 배정하는 화면",
        "questions": [
            "공급 정류소와 수요 정류소는 어떻게 구분하나요?",
            "최대 이동 거리와 최소 이동 수량은 결과에 어떤 영향을 주나요?",
            "차량별 작업 묶음과 방문 순서는 어떻게 해석하나요?",
            "고장 의심 정류소는 차량 경로에 어떤 기준으로 포함되나요?",
            "직선거리 기준 경로와 실제 도로 경로는 어떻게 다른가요?",
        ],
    },
    "settings": {
        "title": "시스템 설정",
        "purpose": "프로모션 및 직접 재배치 시뮬레이션에 적용할 운영 정책과 제한 조건을 관리하는 화면",
        "questions": [
            "프로모션 참여율을 변경하면 어떤 결과가 다시 계산되나요?",
            "직접 재배치의 최대 작업 묶음 수는 무엇인가요?",
            "기본값과 실제 적용값은 어떻게 다른가요?",
            "설정을 바꾼 뒤 결과를 확인하려면 어떤 순서로 진행하나요?",
        ],
    },
    "ai_report": {
        "title": "AI 운영 분석",
        "purpose": "하루 전체 운영 전망과 기준 시점의 실제 이용 실적·현재 재고·근접 운영 위험, 프로모션 시뮬레이션 및 직접 재배치 결과를 종합해 운영 판단을 지원하는 화면",
        "questions": [
            "최근 실제 대여·반납 실적이 예측보다 낮거나 높으면 어떻게 해석하나요?",
            "현재 공급부족 상태와 이후 3시간 운영 위험은 어떻게 구분해서 봐야 하나요?",
            "프로모션과 직접 재배치의 효과를 어떤 기준으로 비교하나요?",
            "현재 추정 재고와 시간대별 예상 종료 재고는 어떻게 다른가요?",
        ],
    },
}

PROJECT_KNOWLEDGE = """
[프로젝트 개요]
이 시스템은 공유 모빌리티 운영 담당자의 의사결정을 지원하는 MVP 솔루션이다.
따릉이 데이터를 민간 공유 PM 운영 상황을 가정한 대리 데이터로 사용한다.

[프로젝트 용어 및 구현 기준]
1. IFI
- 이 프로젝트에서 IFI는 정류소별 예측 재고의 분석 기간 내 최대값과 최소값의 차이다.
- 계산식: IFI = 최대 시간대별 예측 재고 - 최소 시간대별 예측 재고
- 대여량, 반납량, 미충족 수요 또는 현재 재고와 같은 개념이 아니다.

2. 동적 재고 범위
- 정류소별 운영 특성과 재고 변동을 고려해 적정 재고의 하한과 상한을 설정한 범위다.
- 모든 정류소에 동일한 기준을 적용하지 않는다.
- 현재 MVP에서는 제한된 과거 관측 데이터와 IFI 기반 기준을 활용한 추정치다.
- 산출 기준(시뮬레이터 구현 기준)
  · 과거 관측 기간: 예측 기준일 직전 최근 7일의 시간대별 재고 이력을 사용한다.
  · IFI 등급과 목표 만족률: 같은 날짜의 정류소를 IFI 크기순으로 10등급으로 나눈다. IFI가 가장 낮은 1등급의 목표 만족률은 80%이고 등급이 1 올라갈 때마다 2%p씩 낮아져 10등급은 62%다. 즉 재고 변동이 큰 정류소일수록 목표 만족률을 낮게 둔다.
  · 상·하한 계산: 정류소·시간대별 과거 재고 분포에서 목표 만족률만큼의 관측값이 범위 안에 들어오도록 분포 기반 하한과 상한을 구한다.
  · 보정: 하한은 최소 5대 이상으로 올리고, 상한은 하한보다 최소 10대 이상 높게 둔다.
  · 과거 관측 이력이 없는 정류소·시간대는 범위를 산출하지 않고 '과거 관측 없음'으로 구분한다.
- 이 범위는 제한된 과거 관측 기간에 기반한 MVP 가정이며, 실제 운영 기준으로 검증된 값이 아니다. 목표 만족률을 실제 서비스 만족률이나 검증된 성능 지표처럼 설명하지 않는다.
- 위 수치(7일, 5대, 10대, 80%~62%)는 시뮬레이터 기본 설정값이다. 화면에서 바꿀 수 있는 설정이 아니다.

3. 공급부족 / 공급과잉 정류소
- 공급부족은 예측 재고가 설정된 하한 재고보다 낮은 상태다.
- 공급과잉은 예측 재고가 설정된 상한 재고보다 높은 상태다.
- 부족량과 초과량, 정류소 수와 정류소-시간 조합 건수를 서로 혼동하지 않는다.

4. 예상 미충족 대여 수요
- 예상 대여 수요 중 시작 재고로 충족하지 못할 것으로 계산된 수요다.
- 공급부족 정류소 수와 같은 지표가 아니다.

5. 프로모션 시뮬레이션
- 이용자의 자발적인 자전거 이동 참여를 유도하는 운영 정책을 가정한 사전 시뮬레이션이다.
- 참여율, 마일리지, 거리 조건은 시스템 설정에서 변경할 수 있다.
- 실제 참여량, 실제 지출 비용 또는 실제 운영 성과를 의미하지 않는다.
- 현재 설정값을 알 수 없는 경우 3%, 5%, 10%를 현재 적용값이라고 단정하지 않는다.
- '기대 이동량'은 계획 이동량에 참여율을 곱한 값(계획 이동량 × 참여율)이다. 평균적인 기대값이므로 소수점이 될 수 있다.
- '예상 마일리지 비용'은 기대 이동량에 이동 거리별 보상 포인트와 포인트당 원화 환산값을 적용해 계산한 기대 비용이다.
- '모의 이동량'은 각 이동 연결의 계획 이동량을 시행 횟수로, 참여율을 확률로 적용한 이항분포에서 추출한 정수값이다. 화면의 모의 결과는 각 연결별 확률 추출을 한 차례 수행한 시뮬레이션 실행 결과다.
- '모의 부족량 감소율'과 '모의 초과량 감소율'은 위 단일 시뮬레이션에서 추출된 모의 이동량을 정류소 재고에 반영한 뒤, 프로모션 미적용 기준의 부족량·초과량과 비교해 계산한다.
- 따라서 기대 이동량·예상 마일리지 비용은 기대값이고, 모의 부족량·초과량 감소율은 단일 확률 시뮬레이션 결과다. 두 지표 묶음의 계산 기준은 서로 다르며, 모의 감소율을 기대 감소율이나 반복 실험 평균으로 설명하지 않는다.
- 사용자가 지표 차이를 물으면 참여율 적용 기대값과 단일 확률 추출 결과의 차이를 먼저 설명하고, 필요하면 계획 이동량·참여율·모의 이동량의 관계를 예시로 설명한다. 예를 들어 계획 이동량 10대에 참여율 3%를 적용하면 기대 이동량은 0.3대지만, 모의 이동량은 연결별 이항분포 추출 결과에 따라 0대·1대 등 정수로 산출될 수 있다.

6. 직접 재배치
- 프로모션 적용 후 남은 공급·수요 불균형을 기준으로 공급 정류소와 수요 정류소를 연결하는 작업이다.
- 최소 이동 수량, 최대 직선거리, 최대 작업 묶음 수 등 설정 조건에 따라 매칭 결과가 달라진다.
- 차량은 현재 MVP에서 2대로 운영한다.
- 정류소 간 직선거리와 Kakao Mobility 도로 주행거리는 서로 다른 값이다.

7. 실적 기반 기준 시점 운영 분석
- 기준 시각 이전의 실제 이용량은 강서구 내부 정상 OD 기록을 정류소·시간대별로 집계한 값이다.
- 최근 2시간 또는 3시간 실적은 동일 시간대의 예측값과 비교하며, 차이는 실적에서 예측을 뺀 값이다.
- 기준 시점의 현재 추정 재고는 초기 재고 + 누적 실제 반납 - 누적 실제 대여로 계산한다.
- 정류소별 상세 분석의 시간대별 재고 곡선은 일일 운영 시뮬레이션에서 계산한 시간대별 예상 종료 재고다. 실제 OD 이용 실적을 반영한 기준 시점의 현재 추정 재고와 계산 기준이 다른 별도 지표다.
- 현재 추정 재고 KPI는 초기 재고와 기준 시각 이전 누적 실제 반납·대여 실적을 반영한다. 시간대별 예상 종료 재고와 현재 추정 재고를 같은 값으로 설명하거나 서로 대체하지 않는다.
- 시간대별 재고 곡선을 물으면 '일일 운영 시뮬레이션 예상 종료 재고'라고 설명하고, 현재 추정 재고와의 차이를 함께 설명한다.
- 실제·예측 대여량과 반납량의 원천은 정상 OD 이용 기록 건수다. 정상 OD 기록 1건은 자전거 1대의 대여 또는 반납에 대응하므로 운영 화면에서는 이동 수량을 대수(대)로 표시한다. 이는 시간대별 이동량이며 해당 시점의 보유 재고와 동일하지 않다. 고유 자전거 ID를 세는 방식이 아니므로 같은 자전거가 여러 번 이용되면 각 이용 기록이 각각 이동량에 포함될 수 있다.
- 최근 대여 차이는 실적-예측이다. 양수는 실제 대여가 예측보다 많고 음수는 적다는 뜻이다. 정류소별 현재 부족·과잉 원인을 대여 차이 하나로 단정하지 않는다.
- 대여·반납 실적이 모두 예측보다 낮더라도 재고 감소 압력이 커졌다고 단정하지 않는다. 출발-도착 차이를 함께 확인하고, 외부 지역 이동이 제외된 내부 OD 지표를 전체 지역의 순재고 변동으로 확대 해석하지 않는다.
- 현재 부족량·초과량 합계는 자전거 수량(대)이며 정류소 수가 아니다. 현재 부족 정류소 수와 부족량 합계를 구분한다.
- 시간대별 각 행은 해당 한 시간 시점의 정류소 수, 근접 3시간을 합산한 상태 건수는 정류소-시간 조합 수로 구분한다.
- 부족·과잉 정류소가 있다고 최적 재배치 매칭을 확정하지 않는다. 거리와 재배치 설정 및 실제 계산 결과가 필요하다.
- 초기 재고 누락 정류소는 프로젝트 정책에 따라 0대로 계산하되 누락 여부를 별도 표시한다.
- 기준 시각 이후는 실제 실적이 아니라 대여·반납 수요 예측을 이용한 잔여 시간 전망이다.
- 미래 시간대의 공급부족·공급과잉 건수는 정류소-시간 조합 건수이므로 고유 정류소 수와 다르다.
- 대시보드의 시간대별 대여량·반납량 그래프는 강서구 내부 정상 OD 기록으로 집계한 실제 이용량과 저장된 모델의 시간대별 예측량을 비교한다.
- 실제 이용량 막대는 선택한 운영 기준 시각 이전 시간대에만 표시한다. 기준 시각 이후는 아직 관측되지 않은 구간이므로 실제 막대가 없고 예측선만 표시된다.
- 운영 기준 시각은 전체 데이터 시간대에서 선택할 수 있으며 기본값은 09:00이다. 09:00은 기본 선택값이지 고정된 분석 시각이 아니다.
- 대여·반납량의 원천은 정상 OD 이용 기록 건수이며, 기록 1건은 자전거 1대의 이동에 대응한다. 따라서 운영 화면의 대여·반납량은 대수(대)로 표시하지만 시간대별 이동량이지 보유 재고와 동일하지 않다. 재고 안정성은 초기 재고와 누적 대여·반납의 순변화를 반영해 별도로 판단한다.
- 강서구 전체 합계에서는 정류소별 예측 오차가 서로 상쇄될 수 있으므로, 전체 이용량 비교만으로 모든 정류소의 예측 정확도를 단정하지 않는다.
- 현재 대시보드에는 시간대별 재고 안정성 흐름 그래프가 표시되지 않는다. 적정 재고 하한·상한 및 공급부족·공급과잉은 정류소별 운영 현황 지표를 기준으로 설명한다.

8. 고장 의심 이용 및 점검 후보
- '고장 의심'은 실제 고장 판정이나 정비 완료 여부가 아니라, 정상 이용 기준에서 벗어난 OD 기록을 규칙으로 분리한 점검 후보 데이터다. 별도의 고장 진단 모델이 고장을 확정한 결과가 아니다.
- 현재 후보 기준은 강서구 유효 정류소 간 기록 중 출발 정류소와 도착 정류소가 같고, 이용 시간이 결측이며, 이용 거리가 결측이거나 100m 미만인 경우다. 정상 이용 데이터는 이용 시간 1분 이상이면서 이용 거리 100m 이상인 기록으로 분리한다.
- 후보 기록은 출발시간 기준의 시작 정류소와 시간대를 사용해 정류소·시간대별로 집계한다. '전체_건수'를 합산한 값이므로 지표 단위는 '건'이며 고유 자전거 수량인 '대'가 아니다.
- 원천 OD에는 개별 자전거 ID가 없다. 같은 자전거가 여러 번 이용되었는지 식별할 수 없으므로 고유 고장 자전거 수, 실제 고장 대수, 실제 회수 수량을 계산할 수 없다.
- 동일 이동이 출발시간 기준과 도착시간 기준으로 각각 집계된 자료가 있을 수 있으므로 중복 방지를 위해 출발시간 기준 집계만 사용한다. 출발시간 기준과 도착시간 기준 건수를 더하지 않는다.
- 화면의 '고장 의심(건)'은 정류소·시간대별 의심 이용 기록 건수다. 표시값이 0건이거나 후보 데이터가 없다는 사실만으로 실제 고장 차량이 없다고 결론 내리지 않는다.
- 운영상으로는 현장 점검 또는 회수 여부를 검토할 후보 위치와 시간대를 찾는 데 활용한다. 의심 이용 건수를 곧바로 회수·수리해야 할 자전거 대수나 작업량으로 환산하지 않으며, 실제 대상과 수량은 현장 확인 또는 개별 기기 식별 정보로 확정해야 한다.
- 화면별 집계 기준을 구분한다. 대시보드·운영 현황은 출발시간·시작 정류소 기준으로 집계한다. 직접 재배치의 점검 방문은 해당 시간에 방문할 정류소를 찾기 위해 도착시간·종료 정류소 기준으로 집계한다. 같은 이용 기록을 출발·도착 기준으로 중복 합산하지 않는다.
- 직접 재배치에서는 선택 시간대의 고장 의심 정류소를 별도 회수 작업량으로 바꾸지 않고 차량의 기존 방문 경로에 '점검 방문'으로 자동 포함한다. 차량이 이미 방문하는 정류소라면 기존 방문에 점검 작업을 합치고, 새로운 정류소라면 추가 직선거리 증가량이 가장 적은 경로 위치에 삽입한다.
- 자동 배치 기준은 직선거리 증가량을 이용한 휴리스틱이며 전체 도로 경로의 전역 최적해가 아니다. 이후 실제 도로 경로는 경로 API로 계산한다. 자동 포함된 정류소 수는 방문해야 할 점검 지점 수이지 고장 자전거 대수나 회수 수량이 아니다.
- '점검 방문'이 경로에 포함되었다는 사실만으로 현장 점검·회수·수리가 완료된 것으로 설명하지 않는다. 실제 대상 기기와 회수 수량은 현장 확인 또는 개별 기기 식별 정보가 있어야 확정할 수 있다.

9. 분석 범위와 단위 구분
- 기준 시점의 현재 부족량·초과량은 '실적 기반 기준 시점 운영 분석 > 현재 운영 상태'의 값을 사용한다.
- 기존 일일 운영 결과에서 하루 전체를 선택한 부족량·초과량 합계는 24시간 정류소-시간 누적값이다. 이를 현재 재고 부족량·초과량으로 설명하지 않는다.
- 현재 공급부족·공급과잉 정류소 수는 고유 정류소 수이며, 하루 전체 상태 기록 수는 정류소-시간 조합 수다.
- 원천 OD 이용 기록은 건수로 집계하며, 기록 1건을 자전거 1대의 이동으로 간주해 운영 화면의 대여량·반납량·미충족 대여 수요는 대수(대)로 표시한다. 이 이동량과 현재 보유 재고·부족량·초과량을 서로 혼동하지 않는다.
- 프로모션의 모의 부족량 감소율·모의 초과량 감소율은 각 연결별 확률 추출을 한 차례 수행한 시뮬레이션 실행 결과를 미적용 기준과 비교한 값이다. 시간대별 조치 직전 대비 차이율과 혼동하지 않는다.
- 기대 이동량 및 모의 이동량은 대수다. 모의 이동 연결 건수는 시뮬레이션에서 이동량이 발생한 연결 행의 개수이며 실제 이용자 참여 건수가 아니다.
- 평균을 말할 때는 실제 평균값을 계산하고 대상 집단을 명시한다. TOP 5만으로 전체 정류소의 평균을 추정하지 않는다.


9. 업무별 상세 안내

① 프로모션 시뮬레이션
- 목적: 이용자의 자발적인 자전거 이동을 유도하는 운영 정책을 가정한 사전 시뮬레이션이다.
- 확인 및 설정 항목: 참여율, 마일리지, 이동 거리 조건 등 시스템 설정값과 프로모션 미적용 기준 대비 모의 부족량·초과량 감소율을 확인한다.
- 실제 참여량, 실제 지출 비용, 실제 운영 성과를 의미하지 않는다. 모의 이동량이나 계산된 비용을 실제 발생값처럼 설명하지 않는다.
- 현재 설정값이 컨텍스트에 제공되지 않았다면 기본 참여율 3%, 5%, 10%를 현재 적용값이라고 단정하지 않는다.
- 질문 예시: "기대 이동량과 모의 부족량 감소율이 왜 다른 기준으로 계산되나요?"
- 답변 기준: 현재 설정값과 해당 설정으로 다시 계산한 시뮬레이션 결과가 필요하다. 결과가 없으면 수치를 추정하지 말고 설정값 확인 및 재실행이 필요하다고 안내한다. 시뮬레이션 결과는 가정에 따른 모의 결과이며 실제 효과와 다를 수 있다고 밝힌다.

② 직접 재배치
- 목적: 프로모션 적용 후 남은 공급·수요 불균형을 해소하기 위해 공급 정류소와 수요 정류소를 연결하는 작업이다.
- 매칭 결과는 최소 이동 수량, 최대 직선거리, 최대 작업 묶음 수 등 시스템 설정에 따라 달라진다.
- 현재 MVP의 차량 수는 2대다.
- 정류소 간 직선거리와 카카오모빌리티 도로 주행거리는 서로 다른 값이며, 하나를 다른 하나처럼 설명하지 않는다.
- 공급·수요 불균형이 확인되었다고 재배치가 자동으로 확정되는 것은 아니다. 설정 조건과 실제 재배치 계산 결과가 필요하다.
- 질문 예시: "공급부족 정류소와 공급과잉 정류소를 연결하는 재배치 매칭을 진행하려면 어떤 조건이 필요한가요?"
- 답변 기준: 최소 이동 수량, 최대 직선거리, 최대 작업 묶음 수, 차량 2대 배정 조건을 설명한다. 현재 설정값이 제공되지 않았다면 구체적인 수치를 단정하지 않는다.

③ 운영 분석: 실적 기반 기준 시점
- 목적: 기준 시각 이전의 실제 이용량과 예측값을 비교하고, 기준 시점의 추정 재고 및 이후 운영 위험을 확인하는 분석이다.
- 기준 시각 이전의 실제 이용량은 강서구 내부 정상 OD 기록을 정류소·시간대별로 집계한다.
- 최근 2~3시간 실적과 같은 시간대의 예측값을 비교하며 차이는 실적에서 예측을 뺀 값이다.
- 현재 추정 재고는 초기 재고 + 누적 실제 반납 - 누적 실제 대여로 계산한다.
- 정류소 상세의 시간대별 재고 곡선은 일일 운영 시뮬레이션의 예상 종료 재고이며, 현재 추정 재고 KPI와 계산 기준이 다르다. 두 지표를 혼동하지 않는다.
- 실적과 예측의 차이는 실제 이용량이 예측보다 많거나 적었는지를 뜻한다. 이 차이 하나만으로 공급 부족이나 과잉의 원인을 단정하지 않는다.
- 현재 부족량·초과량 합계는 자전거 대수이며 정류소 수가 아니다.
- 한 시간 단위의 정류소 수와 여러 시간대를 합산한 정류소·시간 조합 수를 구분한다.
- 질문 예시: "최근 대여 실적이 예측보다 높으면 공급 부족의 원인이라고 볼 수 있나요?"
- 답변 기준: 실제 대여가 예측보다 많았다는 사실만으로 공급 부족의 원인이라고 단정할 수 없다. 실제 반납량, 출발·도착 간 순이동, 현재 추정 재고와 적정 재고 범위를 함께 확인해야 한다.

[지원 범위]
- 이 도우미는 공유 PM·공유 자전거 운영과 현재 프로젝트의 기능, 데이터, 수요 예측, 재고 관리, 프로모션, 직접 재배치, 경로, 운영 분석에 관한 질문만 답변한다.
- 금융, 정치, 일반 상식, 다른 산업의 이론 등 PM 운영 및 현재 프로젝트와 무관한 질문에는 답변하지 않고 지원 범위를 안내한다.
- 프로젝트에 사용되지 않는 이론이나 개념을 프로젝트 기능과 억지로 연결하지 않는다. 예를 들어 프로젝트에서 다루지 않는 가설·이론을 사용 중인 분석 기법인 것처럼 설명하지 않는다.

[답변 원칙]
- 프로젝트 내부 용어와 구현 기준을 우선 적용한다.
- 일반적인 업계 정의와 프로젝트에서 사용하는 정의가 다르면 구분해서 설명한다.
- 사용자가 익숙하지 않은 용어를 물으면 먼저 쉬운 말로 설명하고 필요할 때 계산 기준을 덧붙인다.
- 수치가 제공되지 않았으면 임의로 만들어내지 않는다.
- 데이터로 확인된 사실, 해석, 가능한 원인, 운영 제안을 구분한다.
- 현재 화면 데이터가 전달되지 않은 경우 실제 화면의 수치를 알고 있는 것처럼 말하지 않는다.
- 설정값의 실제 적용 여부를 확인할 수 없다면 확인이 필요하다고 설명한다.
- 한국어로, 신입 운영자도 이해할 수 있는 표현을 사용한다.
""".strip()



# 프로젝트와 직접 관련된 질문인지 API 호출 전에 1차 확인한다.
PM_TOPIC_KEYWORDS = (
    "공유 pm", "공유pm", "pm 운영", "공유 모빌리티", "마이크로모빌리티", "따릉이",
    "킥보드", "전동킥보드", "공유자전거", "자전거", "대여소", "정류소",
    "대여량", "반납량", "대여 수요", "반납 수요", "미충족 대여 수요",
    "미충족수요", "공급부족", "공급 부족", "공급과잉", "공급 과잉",
    "부족량", "초과량", "재고", "하한재고", "상한재고", "적정 재고",
    "동적 재고", "ifi", "재배치", "프로모션", "마일리지", "참여율",
    "이동량", "차량 배정", "차량별", "배차", "작업 묶음", "방문 순서",
    "도로 경로", "카카오모빌리티", "운영 대시보드", "운영 현황",
    "운영 분석", "운영 도우미", "운영 솔루션", "공급 불균형", "대시보드", "kpi",
    "시간대별 재고 곡선", "예상 종료 재고", "현재 추정 재고",
    "수요 예측", "대여 예측", "반납 예측", "예측 재고", "실제 재고",
    "초기 재고", "기준 시각", "정류소별", "정류소-시간", "정류소 시간",
    "운영 정책", "우선순위", "이용 실적", "대여 실적", "반납 실적",
    "시뮬레이션", "재고 범위", "인벤토리", "플릿", "차량 운영",
    "기기 고장", "고장 의심", "고장 의심 건수", "의심 이용", "점검 후보", "회수 후보",
    "배터리", "정비", "수요 예측 모델", "랜덤포레스트",
    "랜덤 포레스트", "회귀분석", "시계열", "mae", "rmse", "r2",
    "데이터 전처리", "모델 성능", "예측 모델", "예측모델",
)

FOLLOW_UP_REFERENCES = (
    "이거", "이것", "이 부분", "이 값", "이 수치", "이 지표", "이 그래프",
    "이 표", "이 기능", "이 결과", "이 기준", "이 화면", "이 페이지",
    "그거", "그것", "그 부분", "그 값", "그 수치", "그 지표", "그 결과",
    "해당 정류소", "해당 결과", "방금 설명", "앞서 설명", "위 내용",
    "위 결과", "현재 화면", "조금 더 자세히", "다시 설명",
)

OUT_OF_SCOPE_MESSAGE = (
    "이 AI 운영 도우미는 공유 PM 운영과 현재 프로젝트에 관한 질문을 지원합니다. "
    "수요 예측, 정류소 재고, 공급 불균형, 프로모션, 직접 재배치, "
    "운영 분석 등과 관련된 질문을 입력해 주세요."
)


def _contains_pm_topic(text: str) -> bool:
    """질문 또는 대화 내용에 프로젝트 관련 주제가 명시되어 있는지 확인한다."""
    normalized = re.sub(r"\s+", " ", str(text)).strip().lower()
    return any(keyword in normalized for keyword in PM_TOPIC_KEYWORDS)


def _is_pm_related(question: str, history: list[dict[str, Any]] | None = None) -> bool:
    """PM 운영·현재 프로젝트 질문만 허용하고, 문맥형 후속 질문은 예외로 허용한다."""
    normalized = re.sub(r"\s+", " ", question).strip().lower()
    if not normalized:
        return False

    if _contains_pm_topic(normalized) or re.search(r"(?<![a-z0-9])pm(?![a-z0-9])", normalized):
        return True

    # 현재 화면의 특정 표·수치·기능을 가리키는 후속 질문은 허용한다.
    if any(reference in normalized for reference in FOLLOW_UP_REFERENCES):
        return True

    # 직전 대화가 프로젝트 관련 질문이었다면 생략형 후속 질문을 허용한다.
    recent_relevant_history = [
        item for item in (history or [])[-6:]
        if item.get("in_scope", True) and item.get("content")
    ]
    if recent_relevant_history and any(
        _contains_pm_topic(item.get("content", ""))
        for item in recent_relevant_history
    ):
        follow_up_starters = ("그럼", "그러면", "그렇다면", "방금 말한", "아까 말한", "왜", "어떻게", "무슨 뜻", "얼마나")
        if len(normalized) <= 15 and normalized.startswith(follow_up_starters):
            return True

    return False


def _normalize_answer_markdown(answer: str) -> str:
    """따옴표 전체를 감싼 불필요한 굵게 표시와 잘못된 줄바꿈을 정리한다."""
    normalized = str(answer or "").replace("\r\n", "\n").replace("\r", "\n").strip()

    # 인용문 전체에 적용된 굵게 표시 제거: **'문구'**, **‘문구’**, **"문구"**, **“문구”**
    quoted_patterns = (
        (r"\*\*\s*'(.+?)'\s*\*\*", r"'\1'"),
        (r"\*\*\s*‘(.+?)’\s*\*\*", r"‘\1’"),
        (r'\*\*\s*"(.+?)"\s*\*\*', r'"\1"'),
        (r"\*\*\s*“(.+?)”\s*\*\*", r"“\1”"),
    )
    for pattern, replacement in quoted_patterns:
        normalized = re.sub(pattern, replacement, normalized, flags=re.DOTALL)

    # 별표가 인용부호에 붙어 출력된 흔한 변형도 보정한다.
    normalized = re.sub(r"(['’])\s*\*\*", r"\1", normalized)
    normalized = re.sub(r"\*\*\s*(['‘])", r"\1", normalized)
    normalized = re.sub(r'(["”])\s*\*\*', r"\1", normalized)
    normalized = re.sub(r'\*\*\s*(["“])', r"\1", normalized)

    # 모델이 제목·항목의 굵게 표시를 줄 중간에서 열고 닫지 않는 경우를 방지한다.
    cleaned_lines = []
    for line in normalized.split("\n"):
        if line.count("**") % 2 == 1 and line.lstrip().startswith(("-", "•", "①", "②", "③", "④")):
            line = line.rstrip() + "**"
        cleaned_lines.append(line)
    return "\n".join(cleaned_lines)


def _normalize_markdown_stream(chunks: Iterable[str]) -> Iterable[str]:
    """문단 단위로 버퍼링해 인용문 서식을 정리하면서 답변을 스트리밍한다."""
    buffer = ""
    for chunk in chunks:
        if not chunk:
            continue
        buffer += chunk
        while "\n\n" in buffer:
            paragraph, buffer = buffer.split("\n\n", 1)
            yield _normalize_answer_markdown(paragraph) + "\n\n"
    if buffer:
        yield _normalize_answer_markdown(buffer)


def _get_config(name: str, default: str | None = None) -> str | None:
    value = None
    try:
        value = st.secrets.get(name)
    except Exception:
        value = None
    return value or os.getenv(name) or default


def _create_client():
    if OpenAI is None:
        raise ImportError("openai 패키지가 설치되지 않았습니다. .venv 환경에 openai를 설치해 주세요.")

    api_key = _get_config("NVIDIA_API_KEY")
    if not api_key:
        raise ValueError("NVIDIA_API_KEY가 설정되지 않았습니다.")

    return OpenAI(
        base_url=NVIDIA_BASE_URL,
        api_key=api_key,
        timeout=90.0,
        max_retries=1,
    )


def _stream_nvidia(messages: list[dict[str, str]], max_tokens: int = 1200) -> Iterable[str]:
    client = _create_client()
    model = _get_config("NVIDIA_MODEL", DEFAULT_MODEL)
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0.2,
        top_p=0.9,
        max_tokens=max_tokens,
        stream=True,
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )

    for chunk in response:
        if not chunk.choices:
            continue
        content = chunk.choices[0].delta.content
        if content:
            yield content


def _serialize_context(context: Any) -> str:
    if context is None:
        return "현재 페이지의 실시간 표·선택값은 아직 AI에 직접 전달되지 않았습니다. 페이지의 기능과 프로젝트 공통 지식에 관해서는 설명할 수 있지만, 실제 수치가 필요한 질문에는 데이터가 제공되지 않았음을 밝혀야 합니다."

    if isinstance(context, str):
        text = context
    else:
        try:
            text = json.dumps(context, ensure_ascii=False, default=str, indent=2)
        except Exception:
            text = str(context)

    if len(text) > MAX_CONTEXT_CHARS:
        text = text[:MAX_CONTEXT_CHARS] + "\n[컨텍스트가 길어 일부 내용은 생략되었습니다.]"
    return text


def _build_messages(page_key: str, page_context: str) -> list[dict[str, str]]:
    page = PAGE_GUIDE.get(page_key, PAGE_GUIDE["dashboard"])
    page_specific_rules = """
[AI 운영 분석 화면의 추가 규칙]
- 현재 시점 수치는 실적 기반 기준 시점 분석의 '현재 운영 상태'를 기준으로 답한다.
- 현재 부족 정류소 수·과잉 정류소 수(개소)와 현재 부족량·초과량(대)을 반드시 구분한다. 자전거 수량을 고유 정류소 수라고 부르지 않는다.
- 하루 전체 운영 결과의 부족량·초과량 합계는 시간대 누적값이므로 현재 시점 값으로 바꾸어 말하지 않는다.
- 최근 실적 차이는 실적-예측이다. 정류소별 양수·음수 부호를 확인하고 최근 대여 차이만으로 현재 재고 부족의 원인을 단정하지 않는다.
- 최근 대여·반납 실적이 모두 낮으면 출발-도착 차이를 함께 검토한다. 내부 OD만 포함된 값은 외부 이동을 포함한 전체 순재고 변화로 확대 해석하지 않는다.
- 프로모션 감소율은 미적용 기준 대비 감소율인지, 시뮬레이션 내 시간대별 조치 직전 대비 차이율인지 구분한다.
- 대여/반납 원천 이용 기록은 건수로 집계하지만 기록 1건은 자전거 1대의 이동에 대응하므로 운영 화면의 이동량은 대수(대)로 표시한다. 시간대별 이동량과 현재 재고 대수를 혼동하지 않고, 이동량에 '명' 단위를 사용하지 않는다.
- 모의 이동 연결 건수는 실제 운영 실적이 아니라 시뮬레이션의 연결 행 개수다.
""" if page_key == "ai_report" else ""
    system_prompt = f"""
너는 공유 PM 운영 솔루션의 공통 AI 운영 도우미다.
현재 사용자가 보고 있는 화면은 '{page['title']}'이다.
이 화면의 목적: {page['purpose']}

{PROJECT_KNOWLEDGE}

[현재 화면 컨텍스트]
{page_context}

{page_specific_rules}
[응답 방식]
- 신입 운영자도 이해할 수 있도록 전문용어는 처음 나올 때 짧게 풀어서 설명한다.
- 사용자의 질문에 먼저 직접 답한 뒤, 필요할 때 계산 기준이나 다음 행동을 설명한다.
- 현재 화면 컨텍스트와 이전 대화의 페이지 정보를 참고한다.
- 프로모션 지표의 계산 기준은 사용자가 묻거나 해석이 필요한 경우에만 설명하고, 먼저 결론을 답한 뒤 기대값과 단일 모의 결과의 차이를 필요한 만큼만 덧붙인다.
- 현재 화면 컨텍스트에 없는 수치나 최신 설정을 추정하지 않는다.
- 프로젝트 지식과 화면 컨텍스트에서 근거를 찾을 수 없는 기능·수치·정책은 만들어내지 말고 확인할 수 없다고 답한다.
- 일반적인 개념을 설명할 때도 현재 프로젝트에서 실제 사용하는 방식과 일반적인 설명을 구분한다.
- 단순 개념 질문에는 불필요하게 긴 보고서를 작성하지 않는다.
- 인용문이나 작은따옴표·큰따옴표로 감싼 문구 전체를 **굵게 표시**로 감싸지 않는다.
- Markdown의 굵게 표시, 제목, 목록 기호는 반드시 열고 닫는 기호를 완성하고 문장 중간에서 끊지 않는다.
- 한국어로 답변한다.
""".strip()

    messages = [{"role": "system", "content": system_prompt}]
    history = st.session_state.get(HISTORY_KEY, [])[-MAX_HISTORY_MESSAGES:]

    for item in history:
        role = item.get("role")
        content = item.get("content", "")
        if role not in {"user", "assistant"} or not content or item.get("in_scope", True) is False:
            continue
        origin = PAGE_GUIDE.get(item.get("page_key", ""), {}).get("title", "이전 화면")
        messages.append({
            "role": role,
            "content": f"[대화 당시 화면: {origin}]\n{content}",
        })

    return messages


def _append_history(history: list[dict[str, Any]], item: dict[str, Any]) -> None:
    history.append(item)
    if len(history) > MAX_HISTORY_MESSAGES:
        del history[:-MAX_HISTORY_MESSAGES]


def _ask_question(question: str, page_key: str, page_context: str) -> None:
    """질문을 검증한 뒤 기존 대화 아래에 사용자 질문과 답변을 순서대로 표시한다."""
    history = st.session_state.setdefault(HISTORY_KEY, [])
    in_scope = _is_pm_related(question, history)

    with st.chat_message("user"):
        st.markdown(question)

    if not in_scope:
        _append_history(history, {
            "role": "user",
            "content": question,
            "page_key": page_key,
            "in_scope": False,
        })
        with st.chat_message("assistant"):
            st.markdown(OUT_OF_SCOPE_MESSAGE)
        _append_history(history, {
            "role": "assistant",
            "content": OUT_OF_SCOPE_MESSAGE,
            "page_key": page_key,
            "in_scope": False,
        })
        return

    if OpenAI is None or not _get_config("NVIDIA_API_KEY"):
        _append_history(history, {
            "role": "user",
            "content": question,
            "page_key": page_key,
            "in_scope": True,
        })
        message = "AI 대화 기능을 사용할 수 없습니다. openai 패키지와 NVIDIA_API_KEY 설정을 확인해 주세요."
        with st.chat_message("assistant"):
            st.markdown(message)
        _append_history(history, {
            "role": "assistant",
            "content": message,
            "page_key": page_key,
            "in_scope": True,
        })
        return

    # 현재 질문을 history에 넣기 전에 이전 대화만으로 요청 메시지를 구성해 중복 전송을 막는다.
    messages = _build_messages(page_key, page_context)
    messages.append({"role": "user", "content": question})
    _append_history(history, {
        "role": "user",
        "content": question,
        "page_key": page_key,
        "in_scope": True,
    })

    try:
        with st.chat_message("assistant"):
            with st.spinner("답변을 생성하고 있습니다..."):
                if hasattr(st, "write_stream"):
                    streamed_answer = st.write_stream(
                        _normalize_markdown_stream(_stream_nvidia(messages, max_tokens=1000))
                    )
                    answer = _normalize_answer_markdown(streamed_answer)
                else:
                    answer = _normalize_answer_markdown(
                        "".join(_stream_nvidia(messages, max_tokens=1000))
                    )
                    st.markdown(answer)

        if answer:
            _append_history(history, {
                "role": "assistant",
                "content": answer,
                "page_key": page_key,
                "in_scope": True,
            })
        else:
            empty_message = "AI 응답이 비어 있습니다. 질문을 다시 입력해 주세요."
            with st.chat_message("assistant"):
                st.markdown(empty_message)
            _append_history(history, {
                "role": "assistant",
                "content": empty_message,
                "page_key": page_key,
                "in_scope": True,
            })

    except Exception as error:
        error_message = f"AI 응답 생성 중 오류가 발생했습니다: {error}"
        with st.chat_message("assistant"):
            st.error(error_message)
        _append_history(history, {
            "role": "assistant",
            "content": error_message,
            "page_key": page_key,
            "in_scope": True,
        })


def _clear_history() -> None:
    st.session_state[HISTORY_KEY] = []
    st.session_state[CONFIRM_CLEAR_KEY] = False


def _request_clear_history() -> None:
    """바로 지우지 않고 삭제 확인 안내를 먼저 표시한다."""
    if st.session_state.get(HISTORY_KEY):
        st.session_state[CONFIRM_CLEAR_KEY] = True


def _cancel_clear_history() -> None:
    st.session_state[CONFIRM_CLEAR_KEY] = False


def _queue_user_question(input_key: str) -> None:
    """폼 제출 질문을 다음 대화 렌더링 순서에 맞춰 대기열에 저장한다."""
    question = str(st.session_state.get(input_key, "")).strip()
    if question:
        st.session_state[PENDING_QUESTION_KEY] = question


def _decorate_dialog(function):
    """설치된 Streamlit 버전에 맞춰 다이얼로그를 생성한다."""
    dialog_factory = getattr(st, "dialog", None)
    if dialog_factory is None:
        dialog_factory = getattr(st, "experimental_dialog", None)
    if dialog_factory is None:
        return function

    kwargs: dict[str, Any] = {"width": "small"}
    try:
        parameters = inspect.signature(dialog_factory).parameters
        if "position" in parameters:
            kwargs["position"] = "right"
        if "on_dismiss" in parameters:
            kwargs["on_dismiss"] = "ignore"
    except (TypeError, ValueError):
        pass

    return dialog_factory("AI 운영 도우미", **kwargs)(function)


@_decorate_dialog
def _render_assistant_dialog(page_key: str, page_context: str) -> None:
    page = PAGE_GUIDE.get(page_key, PAGE_GUIDE["dashboard"])
    history = st.session_state.setdefault(HISTORY_KEY, [])

    st.markdown(
        """
        <style>
        /* 대화 이력만 스크롤되도록 하고 입력 패널은 그 아래에 남긴다. */
        .st-key-operations-assistant-history {
            max-height: calc(100vh - 240px) !important;
            min-height: 220px !important;
            overflow-y: auto !important;
            overscroll-behavior: contain !important;
            padding-right: 5px !important;
        }
        .st-key-operations-assistant-composer {
            position: relative !important;
            flex: 0 0 auto !important;
            z-index: 5 !important;
            background: var(--background-color) !important;
            padding: 0.7rem 0 0.15rem 0 !important;
            margin-top: 0.45rem !important;
            border-top: 1px solid rgba(128, 128, 128, 0.28) !important;
        }
        .st-key-operations-assistant-composer [data-testid="stExpander"] {
            margin-bottom: 0.4rem !important;
        }
        .st-key-operations-assistant-composer [data-testid="stForm"] {
            border: 0 !important;
            padding: 0 !important;
        }
        .st-key-operations-assistant-composer [data-testid="stTextArea"] {
            margin-bottom: 0.35rem !important;
        }
        .st-key-operations-assistant-composer [data-testid="stTextArea"] textarea {
            white-space: pre-wrap !important;
            overflow-wrap: anywhere !important;
            resize: none !important;
        }

        /* 기록 지우기 버튼: 대화창 제목줄 오른쪽, 닫기(X) 버튼의 왼쪽에 둔다. */
        .st-key-operations-assistant-clear {
            position: absolute !important;
            top: 1.5rem !important; 
            right: 4.25rem !important; 
            width: auto !important;
            z-index: 20 !important;
        }
        .st-key-operations-assistant-clear button {
            min-height: 2rem !important;
            padding: 0 0.7rem !important; 
            font-size: 0.85rem !important;
            white-space: nowrap !important;
        }

        /* 삭제 확인: 대화창 가운데에 별도 팝업처럼 띄운다. */
        .st-key-operations-assistant-confirm {
            position: absolute !important;
            top: 40% !important;
            left: 50% !important;
            transform: translate(-50%, -50%) !important;
            width: min(92%, 360px) !important;
            z-index: 1000 !important;
            padding: 1.1rem 1.1rem 0.9rem 1.1rem !important;
            /* 라이트/다크 테마와 관계없이 글자가 보이도록 배경·글자색을 함께 지정한다. */
            background: #ffffff !important;
            color: #262730 !important;
            border: 1px solid rgba(128, 128, 128, 0.4) !important;
            border-radius: 12px !important;
            box-shadow: 0 10px 40px rgba(0, 0, 0, 0.35) !important;
        }
        .st-key-operations-assistant-confirm p,
        .st-key-operations-assistant-confirm [data-testid="stMarkdownContainer"],
        .st-key-operations-assistant-confirm [data-testid="stCaptionContainer"] {
            color: #262730 !important;
        }
        .st-key-operations-assistant-confirm [data-testid="stCaptionContainer"] {
            color: #5f6270 !important;
        }
        .st-key-operations-assistant-confirm button[kind="secondary"] {
            background: #ffffff !important;
            color: #262730 !important;
            border: 1px solid rgba(49, 51, 63, 0.3) !important;
        }
        .st-key-operations-assistant-confirm button[kind="secondary"] p {
            color: #262730 !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    # 기록 지우기 버튼은 CSS로 제목줄(닫기 버튼 왼쪽)에 배치한다.
    with st.container(key="operations-assistant-clear"):
        st.button(
            "기록 지우기",
            key="operations_assistant_clear_history",
            on_click=_request_clear_history,
        )

    # 삭제 확인 팝업: Streamlit은 대화창 안에 대화창을 또 열 수 없어 가운데 오버레이로 표시한다.
    if st.session_state.get(CONFIRM_CLEAR_KEY):
        with st.container(key="operations-assistant-confirm"):
            st.markdown("**대화 기록을 삭제하시겠습니까?**")
            st.caption("삭제 후에는 복원하기 어렵습니다.")
            confirm_columns = st.columns(2)
            with confirm_columns[0]:
                st.button(
                    "삭제",
                    key="operations_assistant_clear_confirm",
                    type="primary",
                    on_click=_clear_history,
                    use_container_width=True,
                )
            with confirm_columns[1]:
                st.button(
                    "취소",
                    key="operations_assistant_clear_cancel",
                    on_click=_cancel_clear_history,
                    use_container_width=True,
                )

    # 고정 높이 컨테이너 안에서 대화만 스크롤한다.
    # 추천 질문과 입력 폼은 이 컨테이너 밖에 있어 대화 길이에 따라 밀려나지 않는다.
    with st.container(height=550, border=False, key="operations-assistant-history"):
        for item in history[-MAX_HISTORY_MESSAGES:]:
            with st.chat_message(item["role"]):
                st.markdown(_normalize_answer_markdown(item["content"]))
        response_slot = st.empty()

    # 입력 영역은 대화 스크롤 컨테이너 바깥에 고정 배치한다.
    selected_prompt = None
    with st.container(key="operations-assistant-composer"):
        with st.expander("추천 질문", expanded=False):
            prompts = page["questions"]
            for index in range(0, len(prompts), 2):
                columns = st.columns(2)
                for offset, column in enumerate(columns):
                    prompt_index = index + offset
                    if prompt_index >= len(prompts):
                        continue
                    with column:
                        if st.button(
                            prompts[prompt_index],
                            key=f"ops_assistant_prompt_{page_key}_{prompt_index}",
                            use_container_width=True,
                        ):
                            selected_prompt = prompts[prompt_index]

        question_input_key = f"operations_assistant_question_{page_key}"
        with st.form("operations_assistant_form", clear_on_submit=True):
            st.text_area(
                "질문",
                key=question_input_key,
                placeholder="예: 공급 부족 정류소는 어떤 기준으로 정해지나요?",
                label_visibility="collapsed",
                height=80,
            )
            st.form_submit_button(
                "질문 보내기",
                type="primary",
                use_container_width=True,
                on_click=_queue_user_question,
                args=(question_input_key,),
            )

    # 직접 입력과 추천 질문 모두 답변은 대화 스크롤 영역 안에 추가한다.
    pending_question = st.session_state.pop(PENDING_QUESTION_KEY, None)
    question_to_ask = selected_prompt or pending_question
    if question_to_ask:
        with response_slot.container():
            _ask_question(str(question_to_ask), page_key, page_context)

    if OpenAI is None or not _get_config("NVIDIA_API_KEY"):
        st.caption("AI 답변을 생성하려면 openai 패키지와 NVIDIA_API_KEY 설정을 확인해 주세요.")


def render_operations_assistant(page_key: str, context: Any = None) -> None:
    """각 페이지에서 호출하면 우측 하단 AI 버튼과 공통 대화창을 표시한다.

    Args:
        page_key: dashboard, operations, promotion, relocation, settings, ai_report 중 하나.
        context: 현재 페이지의 선택값이나 주요 지표를 전달할 때 사용한다.
    """
    if page_key not in PAGE_GUIDE:
        raise ValueError(f"지원하지 않는 page_key입니다: {page_key}")

    st.session_state.setdefault(HISTORY_KEY, [])
    page_context = _serialize_context(context)

    st.markdown(
        """
        <style>
        .st-key-operations-assistant-fab {
            position: fixed !important;
            right: 26px !important;
            bottom: 24px !important;
            z-index: 100000 !important;
            width: auto !important;
            max-width: calc(100vw - 40px) !important;
        }
        .st-key-operations-assistant-fab button {
            border-radius: 999px !important;
            min-height: 50px !important;
            padding: 0 20px !important;
            white-space: nowrap !important;
            box-shadow: 0 4px 16px rgba(0, 0, 0, 0.22) !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    with st.container(key="operations-assistant-fab"):
        open_assistant = st.button(
            "💻 AI 운영 도우미",
            key=f"open_operations_assistant_{page_key}",
            type="primary",
        )

    if open_assistant:
        _render_assistant_dialog(page_key, page_context)