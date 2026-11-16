from __future__ import annotations

import shutil
import uuid

import streamlit as st

from utils import promotion_simulator as promotion_simulator


def render_promotion_settings() -> None:
    """시스템 설정 페이지에 프로모션 설정 UI와 저장·재실행 기능을 표시한다."""
    try:
        current_settings = promotion_simulator.load_current_promotion_settings()
    except Exception as error:
        st.error(f"저장된 프로모션 설정을 읽지 못했습니다: {error}")
        st.info("설정 파일을 확인한 뒤 다시 시도해 주세요.")
        return

    st.subheader("프로모션 운영 설정")
    st.caption(
        "참여율, 대상 우선구간, 이동 거리 및 마일리지 보상 기준을 설정합니다. "
        "저장하면 해당 설정으로 프로모션 시뮬레이션을 다시 실행합니다."
    )

    with st.form("promotion_settings_form"):
        st.markdown("**단계별 참여율 및 대상 우선구간**")

        participation_columns = st.columns(3)
        participation_rates = {}
        for stage, column in zip((1, 2, 3), participation_columns):
            with column:
                participation_rates[stage] = st.number_input(
                    f"{stage}단계 참여율 (%)",
                    min_value=0.0,
                    max_value=100.0,
                    value=float(current_settings["participation_rates"][stage] * 100),
                    step=1.0,
                    key=f"promotion_participation_{stage}",
                )

        rule_columns = st.columns(3)
        stage_rules = {}
        for stage, column in zip((1, 2, 3), rule_columns):
            with column:
                st.markdown(f"**{stage}단계 우선구간**")
                stage_rules[stage] = {
                    "max_source_band": st.number_input(
                        "공급 우선구간 상한",
                        min_value=1,
                        max_value=10,
                        value=int(current_settings["stage_rules"][stage]["max_source_band"]),
                        step=1,
                        key=f"promotion_source_band_{stage}",
                    ),
                    "min_target_band": st.number_input(
                        "수요 우선구간 하한",
                        min_value=1,
                        max_value=10,
                        value=int(current_settings["stage_rules"][stage]["min_target_band"]),
                        step=1,
                        key=f"promotion_target_band_{stage}",
                    ),
                }

        st.divider()
        st.markdown("**이동 거리 기준**")
        distance_columns = st.columns(3)
        with distance_columns[0]:
            min_distance_km = st.number_input(
                "최소 이동 거리 (km)",
                min_value=0.0,
                value=float(current_settings["min_distance_km"]),
                step=0.1,
                format="%.1f",
                key="promotion_min_distance",
            )
        with distance_columns[1]:
            bonus_distance_threshold_km = st.number_input(
                "추가 보상 기준 거리 (km)",
                min_value=0.0,
                value=float(current_settings["bonus_distance_threshold_km"]),
                step=0.1,
                format="%.1f",
                key="promotion_bonus_threshold",
            )
        with distance_columns[2]:
            max_distance_km = st.number_input(
                "최대 이동 거리 (km)",
                min_value=0.0,
                value=float(current_settings["max_distance_km"]),
                step=0.1,
                format="%.1f",
                key="promotion_max_distance",
            )

        st.divider()
        st.markdown("**마일리지 보상 기준**")
        mileage_columns = st.columns(4)
        with mileage_columns[0]:
            base_mileage_points = st.number_input(
                "기본 마일리지 (P)",
                min_value=0,
                value=int(current_settings["base_mileage_points"]),
                step=10,
                key="promotion_base_mileage",
            )
        with mileage_columns[1]:
            bonus_mileage_1_points = st.number_input(
                "1차 추가 보상 (P)",
                min_value=0,
                value=int(current_settings["bonus_mileage_1_points"]),
                step=10,
                key="promotion_bonus_mileage_1",
            )
        with mileage_columns[2]:
            bonus_mileage_2_points = st.number_input(
                "2차 추가 보상 (P)",
                min_value=0,
                value=int(current_settings["bonus_mileage_2_points"]),
                step=10,
                key="promotion_bonus_mileage_2",
            )
        with mileage_columns[3]:
            point_value_krw = st.number_input(
                "마일리지 환산 가치 (원/P)",
                min_value=0.0,
                value=float(current_settings["point_value_krw"]),
                step=0.1,
                format="%.1f",
                key="promotion_point_value",
            )

        submitted = st.form_submit_button(
            "설정 저장 및 시뮬레이션 재실행",
            type="primary",
            use_container_width=True,
        )

    if not submitted:
        return

    new_settings = {
        "participation_rates": {
            stage: participation_rates[stage] / 100
            for stage in (1, 2, 3)
        },
        "stage_rules": stage_rules,
        "min_distance_km": min_distance_km,
        "bonus_distance_threshold_km": bonus_distance_threshold_km,
        "max_distance_km": max_distance_km,
        "base_mileage_points": base_mileage_points,
        "bonus_mileage_1_points": bonus_mileage_1_points,
        "bonus_mileage_2_points": bonus_mileage_2_points,
        "point_value_krw": point_value_krw,
    }

    try:
        validated_settings = promotion_simulator.resolve_promotion_settings(new_settings)
    except Exception as error:
        st.error(f"설정값을 저장할 수 없습니다: {error}")
        return

    target_dir = None
    staging_dir = None
    backup_dir = None

    try:
        with st.spinner("프로모션 시뮬레이션을 실행하고 결과를 갱신하고 있습니다..."):
            operations_result = promotion_simulator.load_latest_operations_result()
            station_info = promotion_simulator.read_csv_auto(promotion_simulator.STATION_PATH)

            # 임시 폴더에서 전체 결과를 생성해 실행 도중 기존 결과가 덮어써지지 않게 한다.
            target_dir = promotion_simulator.get_promotion_output_dir(operations_result)
            target_dir.parent.mkdir(parents=True, exist_ok=True)
            run_id = uuid.uuid4().hex
            staging_dir = target_dir.parent / f".{target_dir.name}.staging-{run_id}"
            backup_dir = target_dir.parent / f".{target_dir.name}.backup-{run_id}"

            if target_dir.exists():
                shutil.copytree(target_dir, staging_dir)
            else:
                staging_dir.mkdir(parents=True, exist_ok=True)

            promotion_simulator.run_all_promotion_stages(
                operations_result=operations_result,
                station_info=station_info,
                promotion_settings=validated_settings,
                output_dir=staging_dir,
            )

            # 전체 결과 생성이 성공한 경우에만 날짜 폴더와 현재 설정을 교체한다.
            if target_dir.exists():
                target_dir.replace(backup_dir)

            try:
                staging_dir.replace(target_dir)
                promotion_simulator.save_current_promotion_settings(validated_settings)
            except Exception:
                if target_dir.exists():
                    shutil.rmtree(target_dir, ignore_errors=True)
                if backup_dir.exists():
                    backup_dir.replace(target_dir)
                raise

            if backup_dir.exists():
                shutil.rmtree(backup_dir, ignore_errors=True)

        st.success(
            "프로모션 설정을 저장하고 시뮬레이션 결과를 갱신했습니다. "
            "프로모션 페이지에서 최신 결과를 확인할 수 있습니다."
        )
    except Exception as error:
        if staging_dir is not None and staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)
        if (
            backup_dir is not None
            and backup_dir.exists()
            and target_dir is not None
            and not target_dir.exists()
        ):
            backup_dir.replace(target_dir)
        st.error(f"프로모션 설정 적용 또는 시뮬레이션 실행에 실패했습니다: {error}")
        st.warning(
            "시뮬레이션이 정상 완료되지 않으면 기존 설정과 결과를 유지하도록 처리했습니다."
        )
