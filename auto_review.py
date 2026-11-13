import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Any, Dict, List, Optional, Tuple
from dotenv import load_dotenv

import numpy as np
import pandas as pd
import xarray as xr
from supabase import Client, create_client

logger = logging.getLogger("auto_review")

TARGET_VAR = "Vcmax25"
REQUIRED_DIMS = ("time", "lat", "lon")
BUCKET_NAME = "pknu_climate_big_data_contest_2026"


def get_supabase_client() -> Client:
    load_dotenv()
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")

    if not url or not key:
        raise ValueError("SUPABASE_URL 및 SUPABASE_KEY가 설정되어 있지 않습니다.")

    return create_client(url, key)


def load_nc(path: str | Path) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """NetCDF 파일 로드 및 유효성 검사"""
    with xr.open_dataset(path) as ds:
        if TARGET_VAR not in ds:
            raise ValueError(f"'{TARGET_VAR}' variable is missing.")
        da = ds[TARGET_VAR]
        missing_dims = [dim for dim in REQUIRED_DIMS if dim not in da.dims]
        if missing_dims:
            raise ValueError(f"Required dimension(s) missing: {', '.join(missing_dims)}")
        missing_coords = [dim for dim in REQUIRED_DIMS if dim not in ds.coords]
        if missing_coords:
            raise ValueError(f"Required coordinate(s) missing: {', '.join(missing_coords)}")
        da = da.transpose(*REQUIRED_DIMS)
        return (
            np.asarray(da.values, dtype=np.float64),
            np.asarray(ds["time"].values),
            np.asarray(ds["lat"].values),
            np.asarray(ds["lon"].values),
        )


def calculate_mae(
    submission: np.ndarray,
    answer: np.ndarray,
    sub_time: np.ndarray,
    ans_time: np.ndarray,
    sub_lat: np.ndarray,
    ans_lat: np.ndarray,
    sub_lon: np.ndarray,
    ans_lon: np.ndarray,
) -> Tuple[float, int]:
    """평가 대상 기간(2016-2019 홀수 달) 필터링 후 MAE 계산"""
    sub_years = sub_time.astype("datetime64[Y]").astype(int) + 1970
    sub_months = sub_time.astype("datetime64[M]").astype(int) % 12 + 1

    eval_mask = (
        (sub_years >= 2016)
        & (sub_years <= 2019)
        & (sub_months % 2 == 1)
    )

    submission = submission[eval_mask]
    sub_time = sub_time[eval_mask]

    if submission.shape != answer.shape:
        raise ValueError(
            f"Shape mismatch: submission={submission.shape}, expected={answer.shape}"
        )

    if not np.array_equal(sub_time, ans_time):
        raise ValueError("Time coordinates do not match.")

    if not np.allclose(sub_lat, ans_lat):
        raise ValueError("Latitude coordinates do not match.")

    if not np.allclose(sub_lon, ans_lon):
        raise ValueError("Longitude coordinates do not match.")

    answer_nan = ~np.isfinite(answer)
    submission_nan = ~np.isfinite(submission)

    if not np.array_equal(answer_nan, submission_nan):
        raise ValueError("The missing-value mask has been modified.")

    valid = ~answer_nan
    if not valid.any():
        raise ValueError("The answer file contains no finite evaluation cells.")

    mae = np.mean(np.abs(submission[valid] - answer[valid]))
    return float(mae), int(valid.sum())


def update_leaderboard(client: Client, result: List[Dict[str, Any]]) -> None:
    """리더보드 테이블 갱신 (한글 팀명 변환 후 RPC 호출)"""
    lookup_rows = client.table("team_name_lookup").select("*").execute()
    df_lookup = pd.DataFrame(lookup_rows.data)

    for row in result:
        matched_row = df_lookup[df_lookup["converted_team_name"] == row["team_name"]]
        if not matched_row.empty:
            row["team_name"] = matched_row["original_team_name"].iloc[0]

        client.rpc(
            "upsert_leaderboard",
            {
                "p_team_name": row["team_name"],
                "p_new_score": float(row["score"]),
            },
        ).execute()
        logger.info(f"리더보드 갱신 완료: {row['team_name']} -> {row['score']}")


def update_statistics(client: Client, result: List[Dict[str, Any]]) -> None:
    """통계 테이블 갱신"""
    lookup_rows = client.table("team_name_lookup").select("*").execute()
    df_lookup = pd.DataFrame(lookup_rows.data)

    for row in result:
        matched_row = df_lookup[df_lookup["converted_team_name"] == row["team_name"]]
        if not matched_row.empty:
            row["team_name"] = matched_row["original_team_name"].iloc[0]

        is_error = row["score"] == 999
        insert_data = {
            "team_name": row["team_name"],
            "score": None if is_error else row["score"],
            "status": "ERROR" if is_error else "OK",
        }
        client.table("statistics").insert(insert_data).execute()
        logger.info(f"통계 저장 완료: {row['team_name']} -> {insert_data['status']}")


def run_auto_review(target_date: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    자동 채점 파이프라인 메인 함수.
    target_date: 'MM-DD' 형식 (기본값: 오늘 날짜 Asia/Seoul)
    """
    client = get_supabase_client()

    if target_date is None:
        curr_date = datetime.now(ZoneInfo("Asia/Seoul"))
        target_date = curr_date.strftime("%m-%d")

    submission_path = f"submissions/{target_date}"
    answer_path = "answer"

    logger.info(f"채점 시작: 날짜={target_date}, 스토리지 경로={submission_path}")

    answer_storage = client.storage.from_(BUCKET_NAME).list(answer_path)
    answer_files = [f for f in answer_storage if f["name"].endswith(".nc")]
    submission_storage = client.storage.from_(BUCKET_NAME).list(submission_path)
    submission_files = [f for f in submission_storage if f["name"].endswith(".nc")]

    if not answer_files:
        raise RuntimeError(f"'{answer_path}' 폴더에 정답 NetCDF 파일이 존재하지 않습니다.")

    if not submission_files:
        logger.warning(f"'{submission_path}' 폴더에 제출된 NetCDF 파일이 없습니다.")
        return []

    with tempfile.TemporaryDirectory() as temp_dir:
        # 1. 정답 파일 다운로드
        answer_file_name = answer_files[0]["name"]
        full_answer_path = f"{answer_path}/{answer_file_name}"
        local_answer_path = os.path.join(temp_dir, answer_file_name)

        logger.info(f"정답 파일 다운로드 중: {answer_file_name}")
        answer_data = client.storage.from_(BUCKET_NAME).download(full_answer_path)
        with open(local_answer_path, "wb") as f:
            f.write(answer_data)

        answer, ans_time, ans_lat, ans_lon = load_nc(local_answer_path)

        # 2. 제출 파일 다운로드 및 채점
        result: List[Dict[str, Any]] = []

        for sf in submission_files:
            filename = sf["name"]
            full_sub_path = f"{submission_path}/{filename}"
            local_sub_path = os.path.join(temp_dir, filename)

            logger.info(f"제출 파일 다운로드 중: {filename}")
            sub_data = client.storage.from_(BUCKET_NAME).download(full_sub_path)
            with open(local_sub_path, "wb") as f:
                f.write(sub_data)

            try:
                if not filename.startswith("pred_Y_") or not filename.endswith(".nc"):
                    raise ValueError("파일명 형식이 올바르지 않습니다 ('pred_Y_팀명.nc').")

                submission, sub_time, sub_lat, sub_lon = load_nc(local_sub_path)
                mae, n_eval = calculate_mae(
                    submission, answer,
                    sub_time, ans_time, sub_lat, ans_lat, sub_lon, ans_lon,
                )
                logger.info(f"채점 완료: {filename} -> MAE={mae:.6f}, 평가격자={n_eval}")
                result.append({
                    "team_name": filename[7:-3],
                    "score": round(mae, 6),
                    "n_eval": n_eval,
                })
            except Exception as e:
                logger.error(f"채점 실패 ({filename}): {e} -> 999점 부여")
                result.append({
                    "team_name": filename[7:-3],
                    "score": 999,
                    "n_eval": 0,
                })

        # 3. 리더보드 및 통계 갱신
        update_leaderboard(client, result)
        update_statistics(client, result)

        logger.info(f"총 {len(result)}개 팀 채점 및 DB 반영 완료.")
        return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    run_auto_review()
