import os
from functools import lru_cache
from typing import Optional

from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

@lru_cache(maxsize=1)
def get_client() -> Client:
    url: Optional[str] = os.getenv("SUPABASE_URL")
    key: Optional[str] = os.getenv("SUPABASE_KEY")

    if not url or not key:
        raise ValueError("SUPABASE_URL과 SUPABASE_KEY 환경변수가 설정되지 않았습니다.")

    return create_client(url, key)
