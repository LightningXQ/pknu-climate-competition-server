-- 1. Supabase Vault에 서버 URL 및 시크릿 토큰 등록
-- <your-server-url>: 배포된 FastAPI 서버 주소 (예: https://cerl-review.up.railway.app 또는 https://your-domain.com)
-- <your-review-secret>: 서버의 REVIEW_SECRET 환경 변수에 설정한 임의의 보안 토큰 문자열

select vault.create_secret('https://<your-server-url>', 'review_server_url');
select vault.create_secret('<your-review-secret>', 'review_secret');

-- (이미 등록되어 있어 수정할 경우: select vault.update_secret('...', 'review_secret');)


-- 2. 필수 PostgreSQL 확장(pg_cron, pg_net) 활성화
create extension if not exists pg_cron;
create extension if not exists pg_net;


-- 3. pg_cron 스케줄 등록
-- 한국 시간(KST, UTC+9) 기준 매일 22:05에 실행
-- UTC 기준으로는 13:05 (분 시 일 월 요일 -> 5 13 * * *)
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


-- [참고 쿼리 모음]
-- 등록된 cron 작업 확인:
-- select * from cron.job;

-- cron 실행 로그/기록 확인:
-- select * from cron.job_run_details order by start_time desc limit 10;

-- pg_net HTTP 요청/응답 기록 확인:
-- select * from net._http_response order by created desc limit 10;

-- cron 작업 삭제 시:
-- select cron.unschedule('auto-review-daily');
