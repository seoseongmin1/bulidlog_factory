"""일일 자동 요약. 상태(좋음/주의/이상)와 숫자는 코드가 정하고, AI는 그 숫자로 쉬운 문장만 쓴다.

AI 키가 없거나 AI가 실패하거나 AI 문장에 주어진 데이터에 없는 숫자가 들어 있으면 코드가 만든 문장을 그대로 쓴다.
"""
import json
from collections import OrderedDict

import openai

from . import analysis, grounding
from .llm import model_name

PF_GOOD = 90.0  # 역률 기준. 이 공장은 하루 역률이 90%를 밑도는 날이 절반이라, 주의는 85% 미만에서만 띄운다.
PF_WARN = 85.0
WARN_DEVIATION_PCT = 50.0  # 이상 판정은 아니지만 평소와 이만큼 다르면 주의로 본다.
SIMILAR_PCT = 10.0
MAX_CACHE = 400

_cache: OrderedDict[tuple[str, str], dict] = OrderedDict()

PROMPT = """당신은 철강 공장 생산직 작업자에게 그날의 전기 사용 상태를 알려 주는 안내자입니다.
아래 '사실'만 보고 아주 쉬운 한국어로 요약하세요.

규칙:
- lines에는 정확히 3개의 문장을 넣고, 각 문장은 40자 안팎으로 짧게 씁니다.
- action에는 작업자가 할 일 하나를 한 문장으로 씁니다. 할 일이 없으면 없다고 씁니다.
- 숫자는 '사실'에 있는 숫자만 그대로 씁니다. 새로 계산하거나 반올림을 바꾸지 마세요.
- kVarh, 부하, 기준선 같은 어려운 말은 쓰지 말고, 역률은 '전기 효율(역률)'이라고 씁니다.
- 원인은 단정하지 말고 '~일 수 있어요'처럼 씁니다. 원화 금액은 쓰지 마세요.
- '요약_문장'에 있는 쉬는 날 이름, 비교 대상(평소 평일/주말), 좋음·낮음 판단은 빠뜨리거나 바꾸지 마세요.
- 존댓말(~예요, ~해요)로 씁니다."""

SCHEMA = {
    "type": "object",
    "properties": {
        "lines": {"type": "array", "items": {"type": "string"}},
        "action": {"type": "string"},
    },
    "required": ["lines", "action"],
    "additionalProperties": False,
}


def _fmt(value: float, digits: int = 0) -> str:
    return f"{value:,.{digits}f}"


def _status(report: dict) -> dict:
    """코드로 판정한다. 휴일에 적게 쓴 날은 이상 점수가 높아도 '주의'로 본다."""
    anomaly = report["이상_판정"]
    pf = report["summary"]["power_factor"]
    if anomaly and not (report["휴일"] and anomaly["방향"] == "낮음"):
        return {"level": "bad", "label": "이상"}
    if anomaly or abs(report["기준선_대비_pct"]) >= WARN_DEVIATION_PCT or (pf is not None and pf < PF_WARN):
        return {"level": "warn", "label": "주의"}
    return {"level": "good", "label": "좋음"}


def _usual(report: dict) -> str:
    kind = "주말" if report["요일"] in ("토", "일") else "평일"
    return f"평소 {kind}"


def _rule_lines(report: dict) -> tuple[list[str], str]:
    s = report["summary"]
    usage, base, dev = s["usage_kwh"], report["기준선_kwh"], report["기준선_대비_pct"]
    usual = _usual(report)
    if dev >= 100:
        compare = f"{usual}({_fmt(base)}kWh)의 {usage / base:.1f}배예요."
    elif abs(dev) < SIMILAR_PCT:
        compare = f"{usual}({_fmt(base)}kWh)와 비슷해요."
    else:
        compare = f"{usual}({_fmt(base)}kWh)보다 {abs(dev):.0f}% {'많아요' if dev > 0 else '적어요'}."
    holiday = f"쉬는 날({report['휴일']})이라 " if report["휴일"] else ""
    lines = [
        f"{holiday}전기를 {_fmt(usage)}kWh 썼어요. {compare}",
        f"가장 많이 쓴 때는 {s['peak_at'][-5:]}이고, 그때 {_fmt(s['peak_kw'])}kW였어요.",
    ]
    pf = s["power_factor"]
    if pf is None:
        lines.append("전기 효율(역률)은 이날 계산할 수 없어요.")
    else:
        verdict = "좋아요" if pf >= PF_GOOD else "조금 낮아요" if pf >= PF_WARN else "낮아요"
        lines.append(f"전기 효율(역률)은 {pf}%로 {verdict}(기준 {PF_GOOD:.0f}%).")

    anomaly = report["이상_판정"]
    if anomaly and report["휴일"] and anomaly["방향"] == "낮음":
        action = "휴일이라 적게 쓴 것으로 보여요. 따로 할 일은 없어요."
    elif anomaly and anomaly["방향"] == "높음":
        action = "작업일지에서 평소와 다른 작업(특근·시운전 등)이 있었는지 확인해 주세요."
    elif anomaly:
        action = "설비가 멈췄거나 쉬는 날이었는지 확인해 주세요."
    elif pf is not None and pf < PF_WARN:
        action = "역률 보상장치(콘덴서)가 제대로 켜지는지 점검해 주세요."
    elif abs(dev) >= WARN_DEVIATION_PCT:
        action = "평소와 사용량이 달라요. 이날 작업 일정을 한 번 확인해 주세요."
    else:
        action = "큰 문제는 없어요. 따로 할 일은 없어요."
    return lines, action


def _ai_lines(client: openai.OpenAI, facts: dict) -> tuple[list[str], str] | None:
    response = client.chat.completions.create(
        model=model_name(),
        max_completion_tokens=4000,
        messages=[
            {"role": "system", "content": PROMPT},
            {"role": "user", "content": "사실:\n" + json.dumps(facts, ensure_ascii=False, indent=1)},
        ],
        response_format={"type": "json_schema", "json_schema": {"name": "briefing", "strict": True, "schema": SCHEMA}},
    )
    try:
        body = json.loads(response.choices[0].message.content or "")
    except json.JSONDecodeError:
        return None
    lines, action = body.get("lines"), body.get("action")
    if not isinstance(lines, list) or len(lines) != 3 or not all(isinstance(x, str) and x.strip() for x in lines):
        return None
    if not isinstance(action, str) or not action.strip():
        return None
    # 사실에 없는 숫자가 하나라도 있으면 AI 문장을 쓰지 않는다.
    if not grounding.check(" ".join([*lines, action]), facts, strict=True)["ok"]:
        return None
    return [x.strip() for x in lines], action.strip()


def daily_briefing(date: str, client: openai.OpenAI | None) -> dict:
    report = analysis.daily_report(date)
    status = _status(report)
    lines, action = _rule_lines(report)
    result = {"date": date, "요일": report["요일"], "휴일": report["휴일"], "status": status, "lines": lines, "action": action, "source": "rule"}

    # 이미 AI로 만든 날짜는 AI를 부르지 않는 빠른 요청에도 그 문장을 준다.
    key = (date, model_name())
    if key in _cache:
        _cache.move_to_end(key)
        return _cache[key]
    if client is None:
        return result

    facts = {"날짜": f"{date} ({report['요일']})", "상태": status["label"], "요약_문장": lines, "할_일": action}
    try:
        ai = _ai_lines(client, facts)
    except openai.OpenAIError:
        return result
    if ai is None:
        return result
    result = {**result, "lines": ai[0], "action": ai[1], "source": "ai"}
    _cache[key] = result
    while len(_cache) > MAX_CACHE:
        _cache.popitem(last=False)
    return result
