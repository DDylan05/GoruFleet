
import pandas as pd
import requests


KAKAO_WAYPOINTS_URL = ("https://apis-navi.kakaomobility.com/v1/waypoints/directions")

MAX_WAYPOINTS = 30
MAX_POINTS_PER_REQUEST = MAX_WAYPOINTS + 2


def prepare_stops(visit_df):
    required = ["차량번호", "방문순서", "정류소_ID","정류소명", "위도", "경도"]

    missing = [col for col in required if col not in visit_df.columns]

    if missing:
        raise ValueError(f"필요한 컬럼이 없습니다: {missing}")

    if visit_df.empty:
        raise ValueError("방문 정류소가 없습니다.")

    visits = visit_df.copy()

    visits["위도"] = pd.to_numeric(visits["위도"], errors="coerce")
    visits["경도"] = pd.to_numeric(visits["경도"], errors="coerce")

    if visits[["위도", "경도"]].isna().any().any():
        raise ValueError("정류소 좌표에 빈 값이 있습니다.")

    vehicle_numbers = visits["차량번호"].dropna().unique()

    if len(vehicle_numbers) != 1:
        raise ValueError("차량 한 대의 방문 목록만 전달해야 합니다.")

    visits = visits.sort_values("방문순서")

    stops = []

    for row in visits.itertuples(index=False):
        stop = {
            "name": str(row.정류소명),
            "station_id": str(row.정류소_ID),
            "x": float(row.경도),
            "y": float(row.위도),
        }

        # 연속으로 같은 좌표를 방문하면 중복 제거
        if stops:
            same_location = (
                abs(stops[-1]["x"] - stop["x"]) < 0.0000001
                and abs(stops[-1]["y"] - stop["y"]) < 0.0000001
            )

            if same_location:
                continue

        stops.append(stop)

    return stops, int(vehicle_numbers[0])


def request_route(stops, api_key, priority="RECOMMEND"):
    origin = stops[0]
    destination = stops[-1]

    payload = {
        "origin": {
            "name": origin["name"],
            "x": origin["x"],
            "y": origin["y"],
        },
        "destination": {
            "name": destination["name"],
            "x": destination["x"],
            "y": destination["y"],
        },
        "priority": priority,
        "alternatives": False,
        "road_details": False,
        "summary": False,
    }

    if len(stops) > 2:
        payload["waypoints"] = [
            {
                "name": stop["name"],
                "x": stop["x"],
                "y": stop["y"],
            }
            for stop in stops[1:-1]
        ]

    headers = {
        "Authorization": f"KakaoAK {api_key}",
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(
            KAKAO_WAYPOINTS_URL,
            headers=headers,
            json=payload,
            timeout=30,
        )

    except requests.RequestException as error:
        raise RuntimeError(f"API 요청 실패: {error}") from error

    try:
        data = response.json()

    except ValueError as error:
        raise RuntimeError(
            f"API 응답을 JSON으로 읽을 수 없습니다. "
            f"HTTP {response.status_code}"
        ) from error

    if response.status_code >= 400:
        message = data.get("msg") or data.get("message") or response.text
        raise RuntimeError(
            f"카카오 API 오류 ({response.status_code}): {message}"
        )

    routes = data.get("routes", [])

    if not routes:
        raise RuntimeError("API 응답에 경로 정보가 없습니다.")

    route = routes[0]

    if route.get("result_code") != 0:
        raise RuntimeError(
            f"경로 탐색 실패: {route.get('result_msg')}"
        )

    return route


def get_vehicle_route(visit_df, api_key, priority="RECOMMEND"):
    """차량 한 대의 방문 순서를 유지하며 도로 경로를 조회한다."""

    if not api_key:
        raise ValueError("카카오 REST API 키가 없습니다.")

    stops, vehicle_number = prepare_stops(visit_df)

    # 방문 장소가 한 곳뿐이면 경로 API 호출 생략
    if len(stops) == 1:
        route_points = pd.DataFrame([{
            "경로구간": 1,
            "경로순서": 1,
            "경도": stops[0]["x"],
            "위도": stops[0]["y"],
        }])

        return {
            "vehicle_number": vehicle_number,
            "route_points": route_points,
            "distance_m": 0,
            "duration_sec": 0,
            "api_calls": 0,
            "stop_count": 1,
            "origin_name": stops[0]["name"],
            "destination_name": stops[0]["name"],
        }

    total_distance = 0
    total_duration = 0
    route_rows = []
    api_calls = 0

    start_index = 0

    # 경유지 30개 제한을 넘으면 연속 구간으로 분할
    while start_index < len(stops) - 1:

        end_index = min(
            start_index + MAX_POINTS_PER_REQUEST - 1,
            len(stops) - 1,
        )

        chunk = stops[start_index:end_index + 1]

        route = request_route(
            chunk,
            api_key,
            priority=priority,
        )

        api_calls += 1

        summary = route.get("summary", {})

        total_distance += int(summary.get("distance", 0))
        total_duration += int(summary.get("duration", 0))

        # 도로별 좌표 추출
        for section in route.get("sections", []):
            for road in section.get("roads", []):

                vertexes = road.get("vertexes", [])

                for index in range(0, len(vertexes) - 1, 2):
                    longitude = float(vertexes[index])
                    latitude = float(vertexes[index + 1])

                    # 연속 구간의 중복 좌표 제거
                    if route_rows:
                        previous = route_rows[-1]

                        same_point = (
                            abs(previous["경도"] - longitude) < 0.0000001
                            and abs(previous["위도"] - latitude) < 0.0000001
                        )

                        if same_point:
                            continue

                    route_rows.append({
                        "경로구간": api_calls,
                        "경로순서": len(route_rows) + 1,
                        "경도": longitude,
                        "위도": latitude,
                    })

        # 다음 요청은 이전 요청의 마지막 정류소부터 시작
        start_index = end_index

    route_points = pd.DataFrame(
        route_rows,
        columns=["경로구간", "경로순서", "경도", "위도"],
    )

    if route_points.empty:
        raise RuntimeError("카카오 API에서 도로 좌표를 받지 못했습니다.")

    return {
        "vehicle_number": vehicle_number,
        "route_points": route_points,
        "distance_m": total_distance,
        "duration_sec": total_duration,
        "api_calls": api_calls,
        "stop_count": len(stops),
        "origin_name": stops[0]["name"],
        "destination_name": stops[-1]["name"],
    }
