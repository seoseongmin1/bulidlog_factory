"""데이터베이스 연결 설정. 주소는 backend/.env의 DATABASE_URL에서만 읽고, 없으면 오류를 낸다(SQLite로 자동 연결하지 않는다)."""
import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import Boolean, Column, DateTime, Float, MetaData, String, Table, create_engine
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import ArgumentError

load_dotenv()

ROOT = Path(__file__).resolve().parents[2]
CSV_PATH = ROOT / "database" / "steel_industry_data.csv"

metadata = MetaData()
readings = Table(
    "energy_readings",
    metadata,
    Column("interval_start", DateTime, primary_key=True),
    Column("usage_kwh", Float, nullable=False),
    Column("lag_kvarh", Float, nullable=False),
    Column("lead_kvarh", Float, nullable=False),
    Column("co2_t", Float, nullable=False),
    Column("load_type", String(10), nullable=False),
    Column("is_weekend", Boolean, nullable=False),
)


class DataUnavailableError(ValueError):
    """데이터베이스에서 데이터를 읽을 수 없을 때 쓴다. 메시지에는 접속 정보를 넣지 않는다."""


def normalize_url(url: str) -> str:
    """Supabase가 알려 주는 postgresql:// 주소를 psycopg 드라이버 주소로 바꾼다."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def database_url() -> str:
    """backend/.env의 DATABASE_URL을 읽는다. 없거나 형식이 틀리면 SQLite로 넘어가지 않고 확인할 것을 알린다."""
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        raise DataUnavailableError(
            "DATABASE_URL이 설정되지 않았습니다. backend/.env 파일이 있는지, "
            "그 안에 DATABASE_URL= 줄이 앞의 # 없이 들어 있는지 확인하세요. 값은 Supabase의 Session pooler 연결 문자열입니다."
        )
    if "[YOUR-PASSWORD]" in url:
        raise DataUnavailableError(
            "DATABASE_URL에 [YOUR-PASSWORD] 표시가 그대로 남아 있습니다. 대괄호까지 지우고 실제 데이터베이스 비밀번호를 넣으세요."
        )
    try:
        make_url(url)
    except ArgumentError:
        raise DataUnavailableError(
            "DATABASE_URL 형식이 올바르지 않습니다. postgresql://사용자:비밀번호@호스트:5432/postgres 형태인지 확인하세요. "
            "비밀번호에 @ : / # 같은 기호가 있으면 %40 %3A %2F %23 처럼 바꿔 써야 합니다."
        ) from None
    return normalize_url(url)


def ensure_sqlite_file(url: str) -> None:
    """없는 SQLite 파일을 연결하면 빈 파일이 새로 생기므로, 연결 전에 먼저 확인한다."""
    parsed = make_url(url)
    if parsed.get_backend_name() != "sqlite":
        return
    path = parsed.database
    if path and path != ":memory:" and not Path(path).is_file():
        raise DataUnavailableError("데이터베이스 파일이 없습니다. 먼저 `python -m app.loader`로 데이터를 넣으세요.")


@lru_cache(maxsize=4)
def get_engine(url: str) -> Engine:
    options = {}
    if url.startswith("postgresql"):
        options["connect_args"] = {"connect_timeout": 10}
    return create_engine(url, pool_pre_ping=True, **options)
