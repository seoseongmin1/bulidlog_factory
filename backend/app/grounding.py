"""AI가 쓴 문장의 숫자가 계산 결과(도구 결과·사실)에 있는지 확인한다.

이 앱의 약속은 "숫자는 프로그램이 계산하고 AI는 설명만 한다"이다. 이 모듈은 그 약속이 지켜졌는지 답변마다 기계적으로 검사한다.

- strict=True: 한눈에 보기·교대 인수인계처럼 AI에게 '주어진 숫자만 그대로 쓰라'고 한 곳. 숫자가 정확히 같아야 한다.
- strict=False: 대화 답변. 답변이 쓴 자릿수로 반올림해 같으면 근거가 있다고 본다(2,014.66 → 2,015는 통과, 193.4 → 194는 실패).
  kWh를 MWh로 바꾼 것처럼 1,000배 차이도 인정한다. 0~10의 정수는 '3가지', 목록 번호처럼 셈에 쓰이는 경우가 많아 검사하지 않는다.
"""
import json
import re

NUMBER = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?")
LIST_MARKER = re.compile(r"^\s*(?:\d+[.)]|#+)\s+", re.M)
SMALL_INT = 10


def tokens(text: str) -> list[str]:
    """문장에서 숫자 표기를 순서대로 꺼낸다. 목록 번호(1. 2.)와 제목 표시는 뺀다."""
    return NUMBER.findall(LIST_MARKER.sub("", text or ""))


def _value(token: str) -> float:
    return float(token.replace(",", ""))


def source_numbers(*sources) -> set[float]:
    """도구 결과(JSON 문자열·dict·list)와 질문 문장 안의 모든 숫자. 날짜 2018-12-16은 2018, 12, 16으로 나뉜다."""
    found: set[float] = set()

    def walk(item):
        if isinstance(item, bool) or item is None:
            return
        if isinstance(item, (int, float)):
            found.add(abs(float(item)))
        elif isinstance(item, str):
            found.update(_value(t) for t in NUMBER.findall(item))
        elif isinstance(item, dict):
            for key, value in item.items():
                walk(str(key))
                walk(value)
        elif isinstance(item, (list, tuple, set)):
            for value in item:
                walk(value)

    for source in sources:
        if isinstance(source, str):
            try:
                walk(json.loads(source))
                continue
            except (json.JSONDecodeError, ValueError):
                pass
        walk(source)
    return found


def _matches(token: str, sources: set[float]) -> bool:
    value = _value(token)
    decimals = len(token.split(".")[1]) if "." in token else 0
    tolerance = 0.5 * 10**-decimals + 1e-9
    return any(abs(s - value) <= tolerance or abs(s / 1000 - value) <= tolerance for s in sources)


def check(text: str, *sources, strict: bool = False) -> dict:
    """text의 숫자 중 sources에서 찾지 못한 것을 돌려준다."""
    found = source_numbers(*sources)
    checked, missing = [], []
    for token in tokens(text):
        value = _value(token)
        if not strict and "." not in token and value <= SMALL_INT:
            continue
        checked.append(token)
        ok = value in found if strict else _matches(token, found)
        if not ok and token not in missing:
            missing.append(token)
    return {
        "checked": len(checked),
        "grounded": sum(1 for t in checked if t not in missing),
        "ungrounded": missing,
        "ok": not missing,
    }
