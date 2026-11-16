"""Streamlit Components v2 기반 카카오 정류소 지도 렌더러.

필요 버전: streamlit >= 1.52.0
JavaScript를 이 모듈에 포함해 별도 index.js 빌드/경로 의존성을 제거한다.
"""

from functools import lru_cache
import re
from typing import Any

import streamlit as st


MAP_HTML = """
<div class="kakao-map-root">
    <div class="kakao-map-status" role="status" aria-live="polite">카카오 지도 로딩 중...</div>
    <div class="kakao-map-legend" aria-label="운영 상태 범례">
        <span class="legend-item"><span class="legend-dot shortage"></span>공급부족</span>
        <span class="legend-item"><span class="legend-dot normal"></span>적정</span>
        <span class="legend-item"><span class="legend-dot surplus"></span>공급과잉</span>
        <span class="legend-item"><span class="legend-dot unavailable"></span>범위 산출 불가</span>
    </div>
    <div class="kakao-map-canvas"></div>
</div>
"""

MAP_CSS = """
.kakao-map-root {
    position: relative;
    width: 100%;
    height: 510px;
    overflow: hidden;
    border-radius: 8px;
    background: var(--st-secondary-background-color);
}
.kakao-map-canvas {
    width: 100%;
    height: 510px;
    border-radius: 8px;
}
.kakao-map-status {
    position: absolute;
    z-index: 10;
    top: 10px;
    left: 10px;
    display: none;
    max-width: calc(100% - 20px);
    padding: 7px 10px;
    border: 1px solid #d8dee8;
    border-radius: 6px;
    background: #ffffff;
    color: #333333;
    font-size: 12px;
    line-height: 1.45;
    box-shadow: 0 1px 5px rgba(0, 0, 0, 0.12);
}
.kakao-map-legend {
    position: absolute;
    z-index: 5;
    top: 10px;
    right: 10px;
    display: flex;
    flex-wrap: wrap;
    gap: 8px 12px;
    max-width: calc(100% - 20px);
    padding: 8px 10px;
    border: 1px solid #e5e7eb;
    border-radius: 7px;
    background: rgba(255, 255, 255, 0.96);
    color: #222222;
    font-size: 12px;
    box-shadow: 0 1px 5px rgba(0, 0, 0, 0.12);
}
.legend-item {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    white-space: nowrap;
}
.legend-dot {
    display: inline-block;
    width: 10px;
    height: 10px;
    flex: 0 0 10px;
    border-radius: 50%;
}
.legend-dot.shortage { background: #DC5050; }
.legend-dot.normal { background: #447DB8; }
.legend-dot.surplus { background: #C99B22; }
.legend-dot.unavailable { background: #999999; }
.station-info {
    min-width: 165px;
    box-sizing: border-box;
    padding: 9px 12px;
    border-radius: 5px;
    background: #FFFFFF !important;
    color: #222222 !important;
    opacity: 1 !important;
    filter: none !important;
    -webkit-filter: none !important;
    font-size: 12px;
    line-height: 1.65;
}
.station-info,
.station-info * {
    opacity: 1 !important;
    filter: none !important;
    -webkit-filter: none !important;
}
.station-info strong { color: #111111 !important; font-size: 13px; }
"""

MAP_JS = r"""
const mapInstances = new WeakMap();
const kakaoSdkPromises = new Map();

function loadKakaoSdk(jsKey) {
  if (window.kakao && window.kakao.maps && window.kakao.maps.Map) {
    return Promise.resolve(window.kakao);
  }

  if (kakaoSdkPromises.has(jsKey)) {
    return kakaoSdkPromises.get(jsKey);
  }

  const promise = new Promise((resolve, reject) => {
    const existingScript = document.getElementById("dashboard-kakao-map-sdk");
    if (existingScript) existingScript.remove();

    const script = document.createElement("script");
    script.id = "dashboard-kakao-map-sdk";
    script.src = "https://dapi.kakao.com/v2/maps/sdk.js?appkey=" + encodeURIComponent(jsKey) + "&autoload=false";
    script.onerror = () => reject(new Error("카카오 지도 SDK를 불러오지 못했습니다. JavaScript 키와 등록 도메인을 확인해 주세요."));
    script.onload = () => {
      if (!window.kakao || !window.kakao.maps || typeof window.kakao.maps.load !== "function") {
        reject(new Error("카카오 지도 SDK 초기화에 실패했습니다."));
        return;
      }
      window.kakao.maps.load(() => resolve(window.kakao));
    };
    document.head.appendChild(script);
  });

  kakaoSdkPromises.set(jsKey, promise);
  promise.catch(() => kakaoSdkPromises.delete(jsKey));
  return promise;
}

function formatNumber(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "정보 없음";
  return Number(value).toLocaleString("ko-KR", { maximumFractionDigits: 1 });
}

function createInfoContent(item) {
  const content = document.createElement("div");
  content.className = "station-info";

  const title = document.createElement("strong");
  title.textContent = item.id + " · " + item.status;
  content.appendChild(title);

  const stock = document.createElement("div");
  stock.textContent = "추정 재고: " + formatNumber(item.stock) + "대";
  content.appendChild(stock);

  const range = document.createElement("div");
  range.textContent = "적정 재고: " + formatNumber(item.lower) + "~" + formatNumber(item.upper) + "대";
  content.appendChild(range);

  const imbalance = document.createElement("div");
  if (item.status === "공급부족") {
    imbalance.textContent = "부족량: " + formatNumber(item.shortage) + "대";
    content.appendChild(imbalance);
  } else if (item.status === "공급과잉") {
    imbalance.textContent = "초과량: " + formatNumber(item.surplus) + "대";
    content.appendChild(imbalance);
  } else if (item.status !== "적정") {
    imbalance.textContent = "적정 재고 범위 확인 불가";
    content.appendChild(imbalance);
  }

  // 고장 여부나 고장 자전거 수가 아니라, 고장 의심 이용 기록의 집계 건수다.
  const suspectCount = Number(item.suspect_usage_count);

  if (Number.isFinite(suspectCount) && suspectCount > 0) {
    const suspectUsage = document.createElement("div");
    suspectUsage.textContent = "고장 의심: " + formatNumber(suspectCount) + "건";
    content.appendChild(suspectUsage);
  }

  return content;
}

function createMarkerImage(kakao, markerColor, opacity) {
  const markerSvg = '<svg xmlns="http://www.w3.org/2000/svg" width="22" height="22" viewBox="0 0 22 22"><circle cx="11" cy="11" r="8" fill="' + markerColor + '" fill-opacity="' + opacity + '" stroke="#FFFFFF" stroke-opacity="' + opacity + '" stroke-width="2"/></svg>';
  return new kakao.maps.MarkerImage(
    "data:image/svg+xml;charset=utf-8," + encodeURIComponent(markerSvg),
    new kakao.maps.Size(22, 22),
    { offset: new kakao.maps.Point(11, 11) }
  );
}

function closeAllInfoWindows(instance, exceptId) {
  Object.keys(instance.infoById).forEach((stationId) => {
    if (stationId !== exceptId) instance.infoById[stationId].close();
  });
}

function openSelectedInfo(instance, stationId) {
  if (!stationId || !instance.map) return;
  const marker = instance.markerById[String(stationId)];
  const info = instance.infoById[String(stationId)];
  if (marker && info) {
    closeAllInfoWindows(instance, String(stationId));

    // 선택 마커(z-index 100)보다 InfoWindow를 위에 표시한다.
    // 마커 SVG의 투명도와 팝업 레이어는 별도로 유지한다.
    info.setZIndex(1000);
    info.open(instance.map, marker);
  }
}

function applyMarkerEmphasis(instance) {
  const focusId = instance.selectedStationId || instance.hoveredStationId;
  const shouldDimOthers = Boolean(focusId && instance.dimUnselectedMarkers);

  Object.entries(instance.markerById).forEach(([stationId, marker]) => {
    const isFocused = Boolean(focusId && stationId === String(focusId));
    const images = instance.markerImageById[stationId];

    // 마커 SVG 이미지에만 투명도를 적용한다. InfoWindow는 별도 객체이므로
    // 팝업 배경과 글씨의 불투명도에는 영향을 주지 않는다.
    marker.setImage(shouldDimOthers && !isFocused ? images.dim : images.normal);
    marker.setZIndex(isFocused ? 100 : 1);
  });

  if (focusId) {
    openSelectedInfo(instance, focusId);
  } else {
    closeAllInfoWindows(instance);
  }
}

function initializeMap(instance, args) {
  const markerData = Array.isArray(args.marker_data) ? args.marker_data : [];
  const jsKey = String(args.js_key || "").trim();
  const mapElement = instance.mapElement;
  const statusBox = instance.statusBox;

  if (!jsKey) {
    statusBox.textContent = "카카오 지도 JavaScript 키가 설정되지 않았습니다.";
    statusBox.style.display = "block";
    return;
  }
  if (!markerData.length) {
    statusBox.textContent = "지도에 표시할 정류소가 없습니다.";
    statusBox.style.display = "block";
    return;
  }

  statusBox.textContent = "카카오 지도 로딩 중...";
  statusBox.style.display = "block";

  const requestId = (instance.requestId || 0) + 1;
  instance.requestId = requestId;

  loadKakaoSdk(jsKey)
    .then((kakao) => {
      if (requestId !== instance.requestId) return;

      Object.values(instance.infoById).forEach((info) => info.close());
      Object.values(instance.markerById).forEach((marker) => marker.setMap(null));
      mapElement.replaceChildren();

      const first = markerData[0];
      const map = new kakao.maps.Map(mapElement, {
        center: new kakao.maps.LatLng(first.lat, first.lng),
        level: 6,
      });
      const bounds = new kakao.maps.LatLngBounds();
      const markerById = {};
      const markerImageById = {};
      const infoById = {};

      markerData.forEach((item) => {
        const position = new kakao.maps.LatLng(item.lat, item.lng);
        bounds.extend(position);

        const markerColors = {
          "공급부족": "#DC5050",
          "적정": "#447DB8",
          "공급과잉": "#C99B22",
          "범위 산출 불가": "#999999",
        };
        const markerColor = markerColors[item.status] || markerColors["범위 산출 불가"];
        const markerImage = createMarkerImage(kakao, markerColor, 1);
        const dimMarkerImage = createMarkerImage(kakao, markerColor, 0.18);
        const marker = new kakao.maps.Marker({
          map,
          position,
          image: markerImage,
          clickable: true,
        });
        const info = new kakao.maps.InfoWindow({
          content: createInfoContent(item),
          removable: true,
        });

        markerById[String(item.id)] = marker;
        markerImageById[String(item.id)] = {
          normal: markerImage,
          dim: dimMarkerImage,
        };
        infoById[String(item.id)] = info;

        kakao.maps.event.addListener(marker, "click", () => {
          instance.selectedStationId = String(item.id);
          instance.awaitingParentSync = true;
          applyMarkerEmphasis(instance);
          instance.setStateValue("selected_station_id", instance.selectedStationId);
        });
        kakao.maps.event.addListener(marker, "mouseover", () => {
          if (!instance.selectedStationId && !instance.hoveredStationId) {
            info.setZIndex(1000);
            info.open(map, marker);
          }
        });
        kakao.maps.event.addListener(marker, "mouseout", () => {
          if (instance.selectedStationId || instance.hoveredStationId) {
            applyMarkerEmphasis(instance);
          } else {
            info.close();
          }
        });
      });

      instance.map = map;
      instance.markerById = markerById;
      instance.markerImageById = markerImageById;
      instance.infoById = infoById;
      instance.signature = JSON.stringify({ jsKey, markerData });
      instance.initializingSignature = null;
      map.setBounds(bounds);
      statusBox.style.display = "none";
      applyMarkerEmphasis(instance);
    })
    .catch((error) => {
      if (requestId !== instance.requestId) return;
      instance.initializingSignature = null;
      statusBox.textContent = "지도 표시 오류: " + error.message;
      statusBox.style.display = "block";
    });
}

export default function (component) {
  const { parentElement, data, setStateValue } = component;
  let instance = mapInstances.get(parentElement);

  if (!instance) {
    instance = {
      statusBox: parentElement.querySelector(".kakao-map-status"),
      mapElement: parentElement.querySelector(".kakao-map-canvas"),
      map: null,
      markerById: {},
      markerImageById: {},
      infoById: {},
      signature: null,
      initializingSignature: null,
      requestId: 0,
      setStateValue,
      selectedStationId: data?.selected_station_id ? String(data.selected_station_id) : null,
      hoveredStationId: data?.hovered_station_id ? String(data.hovered_station_id) : null,
      awaitingParentSync: false,
      parentSelectionRevision: Number(data?.selection_revision || 0),
      dimUnselectedMarkers: data?.dim_unselected_markers !== false,
    };
    mapInstances.set(parentElement, instance);
  }

  const markerData = Array.isArray(data?.marker_data) ? data.marker_data : [];
  const jsKey = String(data?.js_key || "").trim();
  const nextSignature = JSON.stringify({ jsKey, markerData });
  const nextSelectedId = data?.selected_station_id ? String(data.selected_station_id) : null;
  const nextHoveredId = data?.hovered_station_id ? String(data.hovered_station_id) : null;
  const nextSelectionRevision = Number(data?.selection_revision || 0);
  const nextDimUnselectedMarkers = data?.dim_unselected_markers !== false;
  instance.setStateValue = setStateValue;

  const previousSelectedId = instance.selectedStationId;
  const previousHoveredId = instance.hoveredStationId;
  const previousDimUnselectedMarkers = instance.dimUnselectedMarkers;
  instance.dimUnselectedMarkers = nextDimUnselectedMarkers;
  const explicitParentSelection = nextSelectionRevision !== instance.parentSelectionRevision;

  if (explicitParentSelection) {
    // 표 선택 등 부모 화면에서 발생한 새 선택은 마커의 이전 로컬 선택보다 우선한다.
    instance.parentSelectionRevision = nextSelectionRevision;
    instance.selectedStationId = nextSelectedId;
    instance.awaitingParentSync = false;
  } else if (instance.awaitingParentSync) {
    // 마커 클릭 직후 부모가 이전 값을 한 번 더 전달하는 동안에는 로컬 선택을 유지한다.
    if (nextSelectedId === instance.selectedStationId) {
      instance.awaitingParentSync = false;
    }
  } else {
    instance.selectedStationId = nextSelectedId;
  }

  instance.hoveredStationId = nextHoveredId;
  const selectionChanged = previousSelectedId !== instance.selectedStationId;
  const hoverChanged = previousHoveredId !== instance.hoveredStationId;
  const emphasisModeChanged = previousDimUnselectedMarkers !== instance.dimUnselectedMarkers;

  if (instance.signature !== nextSignature && instance.initializingSignature !== nextSignature) {
    instance.initializingSignature = nextSignature;
    initializeMap(instance, data || {});
  } else if (instance.signature === nextSignature && (selectionChanged || hoverChanged || emphasisModeChanged)) {
    applyMarkerEmphasis(instance);
  } else if (instance.signature === nextSignature && instance.selectedStationId) {
    openSelectedInfo(instance, instance.selectedStationId);
  }

  return () => {
    instance.requestId += 1;
    Object.values(instance.infoById).forEach((info) => info.close());
    Object.values(instance.markerById).forEach((marker) => marker.setMap(null));
    if (instance.map) instance.map = null;
    mapInstances.delete(parentElement);
  };
}
"""


@lru_cache(maxsize=1)
def _get_kakao_map_component():
    """Components v2를 등록한다. 별도 index.js 파일이나 v1 declare_component를 사용하지 않는다."""
    component_api = getattr(getattr(st, "components", None), "v2", None)
    component_factory = getattr(component_api, "component", None)
    if not callable(component_factory):
        version = getattr(st, "__version__", "확인 불가")
        raise RuntimeError(
            f"현재 Streamlit 버전({version})은 카카오지도 클릭 선택 기능에 필요한 "
            "Components v2를 지원하지 않습니다. 프로젝트 가상환경에서 "
            "python -m pip install --upgrade 'streamlit>=1.52.0' 을 실행해 주세요."
        )

    version_text = str(getattr(st, "__version__", "1.52.0"))
    version_numbers = tuple(int(value) for value in re.findall(r"\d+", version_text)[:3])
    if version_numbers and version_numbers < (1, 52, 0):
        raise RuntimeError(
            f"현재 Streamlit {version_text}은 Components v2 최소 요구 버전보다 낮습니다. "
            "python -m pip install --upgrade 'streamlit>=1.52.0' 을 실행해 주세요."
        )

    return component_factory(
        name="kakao_station_map",
        html=MAP_HTML,
        css=MAP_CSS,
        js=MAP_JS,
    )


def render_kakao_station_map(
    marker_data: list[dict[str, Any]],
    js_key: str,
    selected_station_id: str | None = None,
    hovered_station_id: str | None = None,
    key: str = "dashboard_kakao_station_map",
    selection_revision: int = 0,
    dim_unselected_markers: bool = True,
) -> str | None:
    """카카오지도를 표시하고 클릭한 정류소 ID를 반환한다."""
    component = _get_kakao_map_component()
    result = component(
        data={
            "marker_data": marker_data,
            "js_key": js_key,
            "selected_station_id": selected_station_id,
            "hovered_station_id": hovered_station_id,
            "selection_revision": int(selection_revision),
            "dim_unselected_markers": bool(dim_unselected_markers),
        },
        default={"selected_station_id": selected_station_id},
        on_selected_station_id_change=lambda: None,
        key=key,
        width="stretch",
        height=510,
    )
    selected_value = getattr(result, "selected_station_id", selected_station_id)
    return str(selected_value) if selected_value else None


PRIORITY_LIST_HTML = """
<div class="priority-list-root">
    <div class="priority-list-guide">행에 마우스를 올리면 지도에서 위치가 강조되고, 클릭하면 상세 정보가 열립니다.</div>
    <div class="priority-list-table-wrap">
        <div class="priority-list-header">
            <span>순위</span><span>정류소 / 확인 사유</span><span>현재 재고</span><span>적정 범위</span><span>불균형</span>
        </div>
        <div class="priority-list-rows"></div>
    </div>
</div>
"""

PRIORITY_LIST_CSS = """
.priority-list-root {
    height: 420px;
    display: flex;
    flex-direction: column;
    color: var(--st-text-color);
    font-size: 12px;
}
.priority-list-guide {
    padding: 0 0 9px 1px;
    color: var(--st-secondary-text-color);
    line-height: 1.45;
}
.priority-list-table-wrap {
    min-height: 0;
    flex: 1;
    overflow: auto;
    border-top: 1px solid var(--st-border-color);
    border-bottom: 1px solid var(--st-border-color);
}
.priority-list-header,
.priority-list-row {
    display: grid;
    grid-template-columns: 42px minmax(112px, 1.35fr) 76px 86px 74px;
    column-gap: 7px;
    align-items: center;
}
.priority-list-header {
    position: sticky;
    top: 0;
    z-index: 2;
    min-height: 34px;
    padding: 0 8px;
    background: var(--st-secondary-background-color);
    border-bottom: 1px solid var(--st-border-color);
    color: var(--st-secondary-text-color);
    font-size: 11px;
    font-weight: 650;
    text-align: center;
}
.priority-list-header span:nth-child(2) { text-align: left; }
.priority-list-row {
    width: 100%;
    min-height: 55px;
    padding: 6px 8px;
    border: 0;
    border-bottom: 1px solid var(--st-border-color);
    background: var(--st-background-color);
    color: var(--st-text-color);
    text-align: center;
    cursor: pointer;
    font: inherit;
}
.priority-list-row:last-child { border-bottom: 0; }
.priority-list-row:hover,
.priority-list-row:focus-visible {
    outline: none;
    background: var(--st-primary-color-light, rgba(49, 51, 63, 0.08));
}
.priority-list-row:focus-visible { box-shadow: inset 0 0 0 2px var(--st-primary-color); }
.priority-rank { font-weight: 700; font-variant-numeric: tabular-nums; }
.priority-station { min-width: 0; text-align: left; }
.priority-station-id { display: block; overflow-wrap: anywhere; font-weight: 650; }
.priority-reason {
    display: inline-block;
    margin-top: 3px;
    padding: 1px 5px;
    border-radius: 4px;
    font-size: 10px;
    line-height: 1.5;
}
.priority-reason.shortage { background: rgba(220, 80, 80, 0.12); color: #C43D3D; }
.priority-reason.surplus { background: rgba(201, 155, 34, 0.14); color: #9B7516; }
.priority-number { white-space: nowrap; font-variant-numeric: tabular-nums; }
.priority-imbalance { font-weight: 700; white-space: nowrap; font-variant-numeric: tabular-nums; }
.priority-empty { padding: 24px 10px; color: var(--st-secondary-text-color); text-align: center; }
@media (max-width: 620px) {
    .priority-list-header, .priority-list-row {
        grid-template-columns: 32px minmax(92px, 1.2fr) 64px 72px 64px;
        column-gap: 4px;
        padding-left: 5px;
        padding-right: 5px;
        font-size: 10px;
    }
}
"""

PRIORITY_LIST_JS = r"""
const priorityListInstances = new WeakMap();

function priorityFormat(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  return Number(value).toLocaleString("ko-KR", { maximumFractionDigits: 0 });
}

function createPriorityCell(className, text) {
  const cell = document.createElement("span");
  cell.className = className;
  cell.textContent = text;
  return cell;
}

function renderPriorityRows(instance, rows) {
  const container = instance.rowsElement;
  container.replaceChildren();

  if (!rows.length) {
    const empty = document.createElement("div");
    empty.className = "priority-empty";
    empty.textContent = "현재 확인할 정류소가 없습니다.";
    container.appendChild(empty);
    return;
  }

  rows.forEach((item) => {
    const row = document.createElement("button");
    row.type = "button";
    row.className = "priority-list-row";
    row.dataset.stationId = String(item.id);
    row.setAttribute("aria-label", String(item.id) + " 정류소 상세 보기");

    row.appendChild(createPriorityCell("priority-rank", String(item.rank)));

    const stationCell = document.createElement("span");
    stationCell.className = "priority-station";
    stationCell.appendChild(createPriorityCell("priority-station-id", String(item.id)));
    const reason = createPriorityCell(
      "priority-reason " + (item.reason === "공급부족" ? "shortage" : "surplus"),
      String(item.reason)
    );
    stationCell.appendChild(reason);
    row.appendChild(stationCell);

    row.appendChild(createPriorityCell("priority-number", priorityFormat(item.stock) + "대"));
    const lower = priorityFormat(item.lower);
    const upper = priorityFormat(item.upper);
    row.appendChild(createPriorityCell("priority-number", lower + "~" + upper + "대"));

    const imbalanceLabel = item.reason === "공급부족" ? "부족 " : "초과 ";
    row.appendChild(createPriorityCell("priority-imbalance", imbalanceLabel + priorityFormat(item.imbalance) + "대"));
    container.appendChild(row);
  });
}

export default function (component) {
  const { parentElement, data, setStateValue } = component;
  let instance = priorityListInstances.get(parentElement);

  if (!instance) {
    instance = {
      rowsElement: parentElement.querySelector(".priority-list-rows"),
      hoveredStationId: null,
      selectedStationId: null,
      rowsSignature: null,
      setStateValue,
      onPointerOver: null,
      onPointerOut: null,
      onClick: null,
    };

    instance.onPointerOver = (event) => {
      const row = event.target.closest("[data-station-id]");
      if (!row || !parentElement.contains(row)) return;
      const relatedRow = event.relatedTarget?.closest?.("[data-station-id]");
      if (relatedRow && relatedRow.dataset.stationId === row.dataset.stationId) return;
      const stationId = String(row.dataset.stationId);
      if (instance.hoveredStationId !== stationId) {
        instance.hoveredStationId = stationId;
        instance.setStateValue("hovered_station_id", stationId);
      }
    };

    instance.onPointerOut = (event) => {
      const row = event.target.closest("[data-station-id]");
      if (!row || !parentElement.contains(row)) return;
      const relatedRow = event.relatedTarget?.closest?.("[data-station-id]");
      if (relatedRow) return;
      if (instance.hoveredStationId !== null) {
        instance.hoveredStationId = null;
        instance.setStateValue("hovered_station_id", null);
      }
    };

    instance.onClick = (event) => {
      const row = event.target.closest("[data-station-id]");
      if (!row || !parentElement.contains(row)) return;
      const stationId = String(row.dataset.stationId);
      instance.selectedStationId = stationId;
      instance.setStateValue("selected_station_id", stationId);
    };

    parentElement.addEventListener("pointerover", instance.onPointerOver);
    parentElement.addEventListener("pointerout", instance.onPointerOut);
    parentElement.addEventListener("click", instance.onClick);
    priorityListInstances.set(parentElement, instance);
  }

  instance.setStateValue = setStateValue;
  instance.hoveredStationId = data?.hovered_station_id ? String(data.hovered_station_id) : null;
  const nextSelectedId = data?.selected_station_id ? String(data.selected_station_id) : null;
  if (nextSelectedId !== instance.selectedStationId) {
    instance.selectedStationId = nextSelectedId;
    instance.setStateValue("selected_station_id", nextSelectedId);
  }
  const rows = Array.isArray(data?.rows) ? data.rows : [];
  const rowsSignature = JSON.stringify(rows);
  if (rowsSignature !== instance.rowsSignature) {
    renderPriorityRows(instance, rows);
    instance.rowsSignature = rowsSignature;
  }

  return () => {
    parentElement.removeEventListener("pointerover", instance.onPointerOver);
    parentElement.removeEventListener("pointerout", instance.onPointerOut);
    parentElement.removeEventListener("click", instance.onClick);
    priorityListInstances.delete(parentElement);
  };
}
"""

@lru_cache(maxsize=1)
def _get_priority_list_component():
    component_api = getattr(getattr(st, "components", None), "v2", None)
    component_factory = getattr(component_api, "component", None)
    if not callable(component_factory):
        version = getattr(st, "__version__", "확인 불가")
        raise RuntimeError(
            f"현재 Streamlit 버전({version})은 우선 확인 목록의 마우스 오버 연동에 필요한 "
            "Components v2를 지원하지 않습니다. python -m pip install --upgrade 'streamlit>=1.52.0' 을 실행해 주세요."
        )

    return component_factory(
        name="dashboard_priority_station_list",
        html=PRIORITY_LIST_HTML,
        css=PRIORITY_LIST_CSS,
        js=PRIORITY_LIST_JS,
    )


def render_priority_station_list(
    rows: list[dict[str, Any]],
    hovered_station_id: str | None = None,
    selected_station_id: str | None = None,
    key: str = "dashboard_priority_station_list",
) -> Any:
    """우선 확인 정류소 목록의 마우스 오버와 클릭 상태를 Streamlit으로 전달한다."""
    component = _get_priority_list_component()
    return component(
        data={
            "rows": rows,
            "hovered_station_id": hovered_station_id,
            "selected_station_id": selected_station_id,
        },
        default={
            "hovered_station_id": hovered_station_id,
            "selected_station_id": selected_station_id,
        },
        on_hovered_station_id_change=lambda: None,
        on_selected_station_id_change=lambda: None,
        key=key,
        width="stretch",
        height=420,
    )
