"""AI 모델 설정. 여러 모듈(에이전트, 한눈에 보기, 교대 인수인계)이 같이 쓰므로 순환 참조가 없게 따로 둔다."""
import os

DEFAULT_MODEL = "gpt-5"


def model_name() -> str:
    return os.getenv("OPENAI_MODEL", "").strip() or DEFAULT_MODEL
