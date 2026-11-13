import logging
import os
from typing import Optional
from fastapi import FastAPI, Header, HTTPException, Query, BackgroundTasks
from pydantic import BaseModel

from auto_review import run_auto_review

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("server")

app = FastAPI(title="CERL Auto Review Server", version="1.0.0")

REVIEW_SECRET = os.environ.get("REVIEW_SECRET")


class ReviewResponse(BaseModel):
    ok: bool
    message: str
    count: Optional[int] = None
    target_date: Optional[str] = None


def verify_secret(authorization: Optional[str] = Header(None)) -> None:
    """Supabase 또는 외부 트리거 요청의 Bearer Token 검증"""
    if not REVIEW_SECRET:
        logger.warning("REVIEW_SECRET 환경 변수가 비어 있어 인증 검사를 건너뜁니다.")
        return

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="인증 헤더가 올바르지 않습니다.")

    token = authorization.split("Bearer ", 1)[1].strip()
    if token != REVIEW_SECRET:
        raise HTTPException(status_code=403, detail="시크릿 토큰이 일치하지 않습니다.")


@app.get("/")
def health_check():
    return {"status": "ok", "service": "CERL Auto Review"}


@app.post("/auto-review", response_model=ReviewResponse)
def trigger_auto_review(
    target_date: Optional[str] = Query(None, description="채점 대상 일자(MM-DD 형식, 기본값: 오늘)"),
    authorization: Optional[str] = Header(None),
):
    """
    동기식 채점 실행 엔드포인트:
    pg_cron + pg_net에서 호출하여 채점을 즉시 수행하고 결과를 응답합니다.
    """
    verify_secret(authorization)
    try:
        result = run_auto_review(target_date=target_date)
        return ReviewResponse(
            ok=True,
            message="채점이 성공적으로 완료되었습니다.",
            count=len(result),
            target_date=target_date,
        )
    except Exception as e:
        logger.exception("채점 중 오류 발생")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/auto-review-async", response_model=ReviewResponse)
def trigger_auto_review_async(
    background_tasks: BackgroundTasks,
    target_date: Optional[str] = Query(None, description="채점 대상 일자(MM-DD 형식, 기본값: 오늘)"),
    authorization: Optional[str] = Header(None),
):
    """
    비동기 채점 실행 엔드포인트:
    pg_net의 타임아웃 방지를 위해 백그라운드 태스크로 즉시 수락하고 채점을 진행합니다.
    """
    verify_secret(authorization)

    def task_wrapper(dt):
        try:
            run_auto_review(target_date=dt)
        except Exception:
            logger.exception("백그라운드 채점 작업 실패")

    background_tasks.add_task(task_wrapper, target_date)
    return ReviewResponse(
        ok=True,
        message="채점 작업이 백그라운드에서 시작되었습니다.",
        target_date=target_date,
    )


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=False)
