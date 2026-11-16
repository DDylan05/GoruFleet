from pathlib import Path
import hashlib
from math import radians, sin, cos, asin, sqrt

import pandas as pd

from utils.relocation_settings import (
    DEFAULT_RELOCATION_SETTINGS,
    load_relocation_settings,
    resolve_relocation_settings,
)

# 기존 pages/relocation.py와의 호환을 위한 기본값 상수
# 실제 매칭 계산은 load_relocation_settings()로 읽은 현재 설정을 사용한다.
MAX_RELOCATION_TASKS = DEFAULT_RELOCATION_SETTINGS["max_work_bundles"]
MAX_PAIR_DISTANCE_KM = DEFAULT_RELOCATION_SETTINGS["max_relocation_distance_km"]
MIN_TRANSFER_QUANTITY = DEFAULT_RELOCATION_SETTINGS["min_transfer_quantity"]


# utils/relocation_planner.py에서 두 단계 위가 프로젝트 최상위 폴더
BASE_DIR = Path(__file__).resolve().parents[1]
STATION_PATH = BASE_DIR / "data" / "서울시 따릉이대여소 마스터 정보.csv"
PROMOTION_RESULTS_DIR = BASE_DIR / "outputs" / "promotion_senario_results"


def read_csv(file_path):
    file_path = Path(file_path)
    if not file_path.exists():
        raise FileNotFoundError(f"파일을 찾을 수 없습니다: {file_path}")

    for encoding in ["utf-8-sig", "cp949", "utf-8"]:
        try:
            return pd.read_csv(file_path, encoding=encoding)
        except UnicodeDecodeError:
            continue
    raise UnicodeError(f"CSV 인코딩을 확인할 수 없습니다: {file_path}")


def normalize_station_id(values):
    return values.astype("string").str.strip().str.replace(r"\.0$", "", regex=True)


def get_result_path(stage, target_date=None, result_paths=None):
    """선택한 프로모션 단계와 날짜에 해당하는 결과 CSV 경로를 반환한다."""
    if stage not in (1, 2, 3):
        raise ValueError("프로모션 단계는 1, 2, 3 중에서 선택해야 합니다.")

    # 기존 호출부에서 명시적 경로를 전달하는 경우도 지원한다.
    if result_paths is not None:
        if stage not in result_paths:
            raise ValueError("프로모션 단계는 1, 2, 3 중에서 선택해야 합니다.")
        path = Path(result_paths[stage])
    else:
        if target_date is None:
            available_dates = get_available_dates(stage)
            if not available_dates:
                raise FileNotFoundError("사용할 수 있는 프로모션 결과가 없습니다.")
            target_date = available_dates[0]

        date_key = pd.to_datetime(target_date).strftime("%Y%m%d")
        path = PROMOTION_RESULTS_DIR / date_key / f"promotion_result_stage{stage}.csv"

    if not path.is_file():
        raise FileNotFoundError(f"프로모션 결과 파일을 찾을 수 없습니다: {path}")
    return path


def get_promotion_result_signature(result_path):
    """프로모션 단계 결과와 적용 설정의 실제 내용을 SHA-256으로 식별한다."""
    result_path = Path(result_path)
    settings_path = result_path.parent / "promotion_settings.json"
    digest = hashlib.sha256()

    for path in (result_path, settings_path):
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")

        if not path.is_file():
            digest.update(b"MISSING")
            continue

        with path.open("rb") as file:
            for chunk in iter(lambda: file.read(1024 * 1024), b""):
                digest.update(chunk)

        digest.update(b"\0")

    return digest.hexdigest()


def get_available_dates(stage, result_paths=None):
    """선택한 단계의 날짜별 프로모션 결과 폴더 목록을 최신순으로 반환한다."""
    if result_paths is not None:
        result = read_csv(get_result_path(stage, result_paths=result_paths))
        if "기준_날짜" not in result.columns:
            raise ValueError("프로모션 결과에 '기준_날짜' 컬럼이 없습니다.")
        dates = pd.to_datetime(result["기준_날짜"], errors="coerce").dropna()
        return sorted(dates.dt.strftime("%Y-%m-%d").unique().tolist(), reverse=True)

    if not PROMOTION_RESULTS_DIR.exists():
        return []

    available_dates = []
    result_name = f"promotion_result_stage{stage}.csv"
    for folder in PROMOTION_RESULTS_DIR.iterdir():
        if not folder.is_dir() or not folder.name.isdigit() or len(folder.name) != 8:
            continue
        if (folder / result_name).is_file():
            try:
                date_text = pd.to_datetime(folder.name, format="%Y%m%d").strftime("%Y-%m-%d")
                available_dates.append(date_text)
            except ValueError:
                continue

    return sorted(set(available_dates), reverse=True)


def get_available_hours(stage, target_date, result_paths=None):
    result = read_csv(get_result_path(stage, target_date, result_paths))
    required = ["기준_날짜", "기준_시간대"]
    missing = [col for col in required if col not in result.columns]
    if missing:
        raise ValueError(f"프로모션 결과에 필요한 컬럼이 없습니다: {missing}")

    result["기준_날짜"] = pd.to_datetime(
        result["기준_날짜"], errors="coerce"
    ).dt.strftime("%Y-%m-%d")
    result["기준_시간대"] = pd.to_numeric(
        result["기준_시간대"], errors="coerce"
    )
    date_text = pd.to_datetime(target_date).strftime("%Y-%m-%d")
    hours = result.loc[
        result["기준_날짜"] == date_text, "기준_시간대"
    ].dropna().astype(int).unique().tolist()
    return sorted(hours)


def load_station_master(station_path=STATION_PATH):
    station = read_csv(station_path)
    required = ["대여소_ID", "위도", "경도"]
    missing = [col for col in required if col not in station.columns]
    if missing:
        raise ValueError(f"정류소 마스터에 필요한 컬럼이 없습니다: {missing}")

    name_col = next(
        (col for col in ["대여소명", "대여소_명", "정류소명", "대여소명칭"]
         if col in station.columns),
        None,
    )
    keep_cols = required + ([name_col] if name_col else [])
    station = station[keep_cols].copy()
    station["대여소_ID"] = normalize_station_id(station["대여소_ID"])
    station["위도"] = pd.to_numeric(station["위도"], errors="coerce")
    station["경도"] = pd.to_numeric(station["경도"], errors="coerce")

    if name_col:
        station = station.rename(columns={name_col: "대여소명"})
    else:
        station["대여소명"] = station["대여소_ID"]

    station = station.drop_duplicates("대여소_ID")
    station = station.dropna(subset=["대여소_ID", "위도", "경도"])
    station = station[(station["위도"] != 0) & (station["경도"] != 0)]
    return station[["대여소_ID", "대여소명", "위도", "경도"]]


def load_promotion_result(stage, target_date, target_hour, result_paths=None):
    result = read_csv(get_result_path(stage, target_date, result_paths))
    required = [
        "기준_날짜", "대여소_ID", "기준_시간대",
        "부족량_조치후", "초과량_조치후",
    ]
    missing = [col for col in required if col not in result.columns]
    if missing:
        raise ValueError(f"프로모션 결과에 필요한 컬럼이 없습니다: {missing}")

    result["기준_날짜"] = pd.to_datetime(
        result["기준_날짜"], errors="coerce"
    ).dt.strftime("%Y-%m-%d")
    result["기준_시간대"] = pd.to_numeric(
        result["기준_시간대"], errors="coerce"
    )
    result["대여소_ID"] = normalize_station_id(result["대여소_ID"])

    date_text = pd.to_datetime(target_date).strftime("%Y-%m-%d")
    selected = result[
        (result["기준_날짜"] == date_text)
        & (result["기준_시간대"] == int(target_hour))
    ].copy()
    if selected.empty:
        raise ValueError(f"선택한 조건의 데이터가 없습니다: {date_text}, {target_hour}시")
    if selected["대여소_ID"].duplicated().any():
        raise ValueError("선택한 날짜와 시간대에 중복 정류소 데이터가 있습니다.")

    for col in ["부족량_조치후", "초과량_조치후"]:
        selected[col] = pd.to_numeric(
            selected[col], errors="coerce"
        ).fillna(0).clip(lower=0).round().astype(int)
    return selected


def haversine_distance(lat1, lon1, lat2, lon2):
    """두 위경도 좌표 사이의 직선거리를 미터로 계산한다."""
    earth_radius = 6371000
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    d_lat, d_lon = lat2 - lat1, lon2 - lon1
    value = sin(d_lat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(d_lon / 2) ** 2
    return 2 * earth_radius * asin(sqrt(value))


def match_supply_demand(
    selected_data,
    station_data,
    relocation_settings=None,
    selected_supply_ids=None,
    selected_demand_ids=None,
    vehicle_assignments=None,
):
    """선택된 공급·수요 정류소만 대상으로 재배치 작업을 매칭한다.

    selected_supply_ids와 selected_demand_ids가 None이면 기존 자동 매칭을 수행한다.
    차량별 소유 정보가 전달되면 같은 차량에 지정된 공급·수요 정류소끼리만 매칭한다.
    ID 목록이 전달되면 해당 정류소만 매칭 대상으로 사용하고, 전체 후보 목록은 결과에 유지한다.
    """
    settings = resolve_relocation_settings(
        load_relocation_settings() if relocation_settings is None else relocation_settings
    )
    max_distance_m = settings["max_relocation_distance_km"] * 1000
    min_transfer_quantity = settings["min_transfer_quantity"]
    max_work_bundles = settings["max_work_bundles"]

    supply = selected_data.loc[
        selected_data["초과량_조치후"] > 0,
        ["대여소_ID", "초과량_조치후"],
    ].rename(columns={
        "대여소_ID": "공급정류소_ID", "초과량_조치후": "공급가능량"
    }).reset_index(drop=True)
    demand = selected_data.loc[
        selected_data["부족량_조치후"] > 0,
        ["대여소_ID", "부족량_조치후"],
    ].rename(columns={
        "대여소_ID": "수요정류소_ID", "부족량_조치후": "필요수량"
    }).reset_index(drop=True)

    lookup = station_data.set_index("대여소_ID").to_dict("index")
    required_ids = set(supply["공급정류소_ID"]) | set(demand["수요정류소_ID"])
    missing_ids = sorted(required_ids - set(lookup))
    if missing_ids:
        raise ValueError(
            "정류소 마스터에서 좌표를 찾을 수 없습니다: " + ", ".join(missing_ids[:10])
        )

    all_supply_ids = supply["공급정류소_ID"].tolist()
    all_demand_ids = demand["수요정류소_ID"].tolist()

    def normalize_id_set(values, fallback_ids):
        if values is None:
            return set(fallback_ids)
        normalized = normalize_station_id(pd.Series(list(values), dtype="string"))
        return set(normalized.dropna().tolist())

    selected_supply_set = normalize_id_set(selected_supply_ids, all_supply_ids)
    selected_demand_set = normalize_id_set(selected_demand_ids, all_demand_ids)
    selected_supply_set.intersection_update(all_supply_ids)
    selected_demand_set.intersection_update(all_demand_ids)

    # 미선정 정류소의 잔여량도 표시할 수 있도록 전체 수량을 보관한다.
    supply_initial = dict(zip(supply["공급정류소_ID"], supply["공급가능량"]))
    demand_initial = dict(zip(demand["수요정류소_ID"], demand["필요수량"]))
    supply_left = supply_initial.copy()
    demand_left = demand_initial.copy()
    supply_vehicle_map = {}
    demand_vehicle_map = {}
    if vehicle_assignments is not None:
        supply_vehicle_map = {
            str(station_id): int(vehicle_number)
            for station_id, vehicle_number in vehicle_assignments.get("supply", {}).items()
        }
        demand_vehicle_map = {
            str(station_id): int(vehicle_number)
            for station_id, vehicle_number in vehicle_assignments.get("demand", {}).items()
        }
        invalid_vehicles = set(supply_vehicle_map.values()) | set(demand_vehicle_map.values())
        if not invalid_vehicles.issubset({1, 2}):
            raise ValueError("정류소별 차량은 1번 또는 2번으로 지정해야 합니다.")

    candidates = []

    for supply_id in all_supply_ids:
        if supply_id not in selected_supply_set:
            continue
        source = lookup[supply_id]

        for demand_id in all_demand_ids:
            if demand_id not in selected_demand_set or supply_id == demand_id:
                continue

            vehicle_number = None
            if vehicle_assignments is not None:
                vehicle_number = supply_vehicle_map.get(supply_id)
                if vehicle_number is None or demand_vehicle_map.get(demand_id) != vehicle_number:
                    continue

            target = lookup[demand_id]
            distance = haversine_distance(
                source["위도"], source["경도"], target["위도"], target["경도"]
            )
            if distance <= max_distance_m:
                candidates.append((distance, supply_id, demand_id, vehicle_number))

    candidates.sort(key=lambda item: (item[0], item[1], item[2]))
    plan_rows = []

    for distance, supply_id, demand_id, vehicle_number in candidates:
        if len(plan_rows) >= max_work_bundles:
            break

        quantity = min(supply_left[supply_id], demand_left[demand_id])
        if quantity < min_transfer_quantity:
            continue

        supply_left[supply_id] -= quantity
        demand_left[demand_id] -= quantity
        source, target = lookup[supply_id], lookup[demand_id]
        plan_row = {
            "공급정류소_ID": supply_id, "공급정류소명": source["대여소명"],
            "수요정류소_ID": demand_id, "수요정류소명": target["대여소명"],
            "이동수량": int(quantity), "직선거리_m": round(distance, 1),
            "공급정류소_위도": source["위도"], "공급정류소_경도": source["경도"],
            "수요정류소_위도": target["위도"], "수요정류소_경도": target["경도"],
        }
        if vehicle_number is not None:
            plan_row["차량번호"] = vehicle_number
        plan_rows.append(plan_row)

    plan_cols = [
        "공급정류소_ID", "공급정류소명", "수요정류소_ID", "수요정류소명",
        "이동수량", "직선거리_m", "공급정류소_위도", "공급정류소_경도",
        "수요정류소_위도", "수요정류소_경도",
    ]
    if vehicle_assignments is not None:
        plan_cols.append("차량번호")
    plan = pd.DataFrame(plan_rows, columns=plan_cols)

    supply["배정수량"] = supply["공급정류소_ID"].map(
        lambda station_id: int(supply_initial[station_id] - supply_left[station_id])
    )
    supply["잔여초과량"] = supply["공급정류소_ID"].map(supply_left)
    demand["충족수량"] = demand["수요정류소_ID"].map(
        lambda station_id: int(demand_initial[station_id] - demand_left[station_id])
    )
    demand["잔여부족량"] = demand["수요정류소_ID"].map(demand_left)

    # 최초 자동 매칭에서는 실제 배정된 정류소를 선택 상태로 표시한다.
    # 운영자 재계산에서는 운영자가 선택한 상태를 그대로 보존한다.
    if selected_supply_ids is None:
        supply["직접재배치_선정여부"] = supply["배정수량"].gt(0)
    else:
        supply["직접재배치_선정여부"] = supply["공급정류소_ID"].isin(selected_supply_set)

    if selected_demand_ids is None:
        demand["직접재배치_선정여부"] = demand["충족수량"].gt(0)
    else:
        demand["직접재배치_선정여부"] = demand["수요정류소_ID"].isin(selected_demand_set)

    supply = supply.merge(
        station_data, left_on="공급정류소_ID", right_on="대여소_ID", how="left"
    ).drop(columns="대여소_ID").rename(columns={
        "대여소명": "정류소명", "위도": "정류소_위도", "경도": "정류소_경도"
    })
    demand = demand.merge(
        station_data, left_on="수요정류소_ID", right_on="대여소_ID", how="left"
    ).drop(columns="대여소_ID").rename(columns={
        "대여소명": "정류소명", "위도": "정류소_위도", "경도": "정류소_경도"
    })
    return plan, supply, demand


def create_relocation_plan(
    stage,
    target_date,
    target_hour,
    result_paths=None,
    station_path=STATION_PATH,
    relocation_settings=None,
    selected_supply_ids=None,
    selected_demand_ids=None,
    vehicle_assignments=None,
):
    """프로모션 결과에서 공급·수요를 읽고 선택 조건에 따라 재배치 계획을 만든다."""
    selected = load_promotion_result(stage, target_date, target_hour, result_paths)
    stations = load_station_master(station_path)
    plan, supply, demand = match_supply_demand(
        selected,
        stations,
        relocation_settings,
        selected_supply_ids,
        selected_demand_ids,
        vehicle_assignments,
    )

    if not plan.empty:
        plan.insert(0, "기준_시간대", int(target_hour))
        plan.insert(0, "기준_날짜", pd.to_datetime(target_date).strftime("%Y-%m-%d"))
    return plan, supply, demand


def assign_vehicles(plan_df, vehicle_count=2):
    """지정 차량이 있으면 유지하고, 없으면 작업 묶음 수가 적은 차량에 배정한다."""
    if vehicle_count < 1:
        raise ValueError("차량 대수는 1대 이상이어야 합니다.")

    required = [
        "공급정류소_ID", "공급정류소명", "수요정류소_ID",
        "수요정류소명", "이동수량", "직선거리_m",
        "공급정류소_위도", "공급정류소_경도",
        "수요정류소_위도", "수요정류소_경도",
    ]
    missing = [col for col in required if col not in plan_df.columns]
    if missing:
        raise ValueError(f"차량 배정에 필요한 컬럼이 없습니다: {missing}")

    assigned = plan_df.copy().reset_index(drop=True)
    if assigned.empty:
        assigned["차량번호"] = pd.Series(dtype="int64")
        assigned["차량작업순번"] = pd.Series(dtype="int64")
        return assigned

    # 운영자가 차량별로 정류소를 지정한 경우 매칭 단계에서 지정된 차량을 유지한다.
    if "차량번호" in assigned.columns:
        assigned["차량번호"] = pd.to_numeric(
            assigned["차량번호"], errors="coerce"
        )
        if assigned["차량번호"].isna().any():
            raise ValueError("일부 작업에 차량이 지정되지 않았습니다.")
        if not assigned["차량번호"].between(1, vehicle_count).all():
            raise ValueError("지정된 차량번호가 허용 범위를 벗어났습니다.")
        assigned["차량번호"] = assigned["차량번호"].astype(int)
        assigned["차량작업순번"] = (
            assigned.groupby("차량번호").cumcount() + 1
        )
        return assigned.sort_values(
            ["차량번호", "차량작업순번"]
        ).reset_index(drop=True)

    job_counts = {vehicle: 0 for vehicle in range(1, vehicle_count + 1)}
    vehicle_numbers = []
    task_orders = []

    # 각 공급-수요 매칭을 하나의 작업 묶음으로 유지한다.
    for _ in range(len(assigned)):
        vehicle = min(job_counts, key=lambda value: (job_counts[value], value))
        vehicle_numbers.append(vehicle)
        job_counts[vehicle] += 1
        task_orders.append(job_counts[vehicle])

    assigned["차량번호"] = vehicle_numbers
    assigned["차량작업순번"] = task_orders

    return assigned.sort_values(
        ["차량번호", "차량작업순번"]
    ).reset_index(drop=True)


def build_vehicle_visit_plan(assigned_plan):
    """작업 묶음을 수거 정류소 방문 → 배치 정류소 방문 순서로 펼친다."""
    required = [
        "차량번호", "차량작업순번", "공급정류소_ID", "공급정류소명",
        "수요정류소_ID", "수요정류소명", "이동수량", "직선거리_m",
        "공급정류소_위도", "공급정류소_경도",
        "수요정류소_위도", "수요정류소_경도",
    ]
    missing = [col for col in required if col not in assigned_plan.columns]
    if missing:
        raise ValueError(f"방문 목록 생성에 필요한 컬럼이 없습니다: {missing}")

    visit_rows = []

    for row in assigned_plan.sort_values(
        ["차량번호", "차량작업순번"]
    ).itertuples(index=False):
        bundle_id = f"V{row.차량번호}-{row.차량작업순번:03d}"
        pickup_order = (row.차량작업순번 - 1) * 2 + 1
        delivery_order = pickup_order + 1

        visit_rows.append({
            "차량번호": row.차량번호,
            "방문순서": pickup_order,
            "작업묶음_ID": bundle_id,
            "방문유형": "수거",
            "정류소_ID": row.공급정류소_ID,
            "정류소명": row.공급정류소명,
            "작업수량": int(row.이동수량),
            "위도": row.공급정류소_위도,
            "경도": row.공급정류소_경도,
            "상대정류소명": row.수요정류소명,
            "묶음거리_m": row.직선거리_m,
        })
        visit_rows.append({
            "차량번호": row.차량번호,
            "방문순서": delivery_order,
            "작업묶음_ID": bundle_id,
            "방문유형": "배치",
            "정류소_ID": row.수요정류소_ID,
            "정류소명": row.수요정류소명,
            "작업수량": int(row.이동수량),
            "위도": row.수요정류소_위도,
            "경도": row.수요정류소_경도,
            "상대정류소명": row.공급정류소명,
            "묶음거리_m": row.직선거리_m,
        })

    columns = [
        "차량번호", "방문순서", "작업묶음_ID", "방문유형",
        "정류소_ID", "정류소명", "작업수량", "위도", "경도",
        "상대정류소명", "묶음거리_m",
    ]
    visits = pd.DataFrame(visit_rows, columns=columns)

    return visits
