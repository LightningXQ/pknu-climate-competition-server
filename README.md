# 기상·기후 환경분야 빅데이터 경진대회 자동 채점 서버

부경대학교 **기후극한연구실(Climate Extremes Research Laboratory, CERL)** 기상·기후 빅데이터 경진대회의 자동 채점(Auto Review) 백엔드 워커 서비스입니다.

Supabase의 `pg_cron` 및 `pg_net` 확장을 통해 매일 마감 시간 직후 트리거되어, 제출된 NetCDF 파일들의 구조 및 오차(MAE)를 계산하고 리더보드/통계를 자동으로 갱신합니다.

---

## 📌 주요 구성 및 파이프라인

1. **`auto_review.py`**
   - Supabase Storage에서 당일 정답(`answer/`) 및 참가자 제출(`submissions/MM-DD/`) NetCDF 파일 다운로드
   - `xarray` 기반 차원/좌표/NaN 마스크 검증 및 2016~2019년 홀수 달 대상 MAE 계산
   - 한글 팀명 lookup 변환 처리 및 Supabase RPC(`upsert_leaderboard`), `statistics` 테이블 일괄 반영

2. **`server.py`**
   - FastAPI 기반 웹 엔드포인트
   - Bearer Token(`REVIEW_SECRET`) 인증 보안 적용
   - `POST /auto-review-async`: `pg_net` 타임아웃 방지를 위한 비동기 백그라운드 채점 작업
   - `POST /auto-review`: 동기식 채점 실행

3. **`supabase_cron_setup.sql`**
   - Supabase Vault 시크릿 등록 및 `pg_cron` 일일 스케줄러 등록 스크립트

---

## 🛠 환경 변수 설정

| 변수명 | 설명 | 예시 |
| :--- | :--- | :--- |
| `SUPABASE_URL` | Supabase 프로젝트 URL | `https://xxxx.supabase.co` |
| `SUPABASE_KEY` | Supabase Service Role Key (Admin 권한) | `sb_secret_...` |
| `REVIEW_SECRET` | pg_net HTTP 요청 인증용 시크릿 토큰 | 임의의 보안 문자열 |
| `PORT` | 서버 리스닝 포트 (기본값: 8000) | `8000` |

---

## 🚀 로컬 실행 방법

```bash
# 1. 의존성 설치
pip install -r requirements.txt

# 2. 서버 실행
python server.py
# 또는
uvicorn server:app --host 0.0.0.0 --port 8000
```

---

## 🐳 Docker 배포

```bash
docker build -t cerl-review-server .
docker run -d -p 8000:8000 \
  -e SUPABASE_URL="https://xxxx.supabase.co" \
  -e SUPABASE_KEY="your-service-role-key" \
  -e REVIEW_SECRET="your-secret-token" \
  cerl-review-server
```

---

## ⏰ Supabase pg_cron 연동

`supabase_cron_setup.sql` 파일을 참고하여 Supabase SQL Editor에서 실행합니다:

```sql
-- 1. Vault에 시크릿 등록
select vault.create_secret('https://<your-deployed-server>', 'review_server_url');
select vault.create_secret('<your-secret-token>', 'review_secret');

-- 2. 매일 22:05 KST (13:05 UTC) 자동 채점 트리거 등록
select cron.schedule(
    'auto-review-daily',
    '5 13 * * *',
    $$
    select net.http_post(
        url     := (select decrypted_secret from vault.decrypted_secrets where name = 'review_server_url') || '/auto-review-async',
        headers := jsonb_build_object(
            'Content-Type',  'application/json',
            'Authorization', 'Bearer ' || (select decrypted_secret from vault.decrypted_secrets where name = 'review_secret')
        ),
        body    := jsonb_build_object('triggered_at', now())
    ) as request_id;
    $$
);
```

---

## 📄 라이선스 및 문의

- **주관**: 부경대학교 기후극한연구실 (Climate Extremes Research Laboratory)
- **홈페이지**: [https://sites.google.com/view/cerl](https://sites.google.com/view/cerl)
- **프로젝트 운영 관련 문의**: ycj1219@pukyong.ac.kr
- **웹 플랫폼 유지·보수 관련 문의**: jihojeong.dev@gmail.com
- **저작권**: © 2026. CERL. All rights reserved.
