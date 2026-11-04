"""교대 인수인계서 자동 작성. 교대가 끝나면 그 조의 사용량·경보·작업자 응답을 모아 다음 조에 넘길 문서를 만든다.

briefing.py와 같은 원칙을 따른다. 숫자와 상태, 할 일은 코드가 정하고, AI는 그 내용을 읽기 쉬운 문장으로만 다듬는다.
AI 문장에 사실에 없는 숫자가 하나라도 있으면 버리고 코드가 만든 문장을 쓴다. API 키가 없어도 인수인계서는 나온다.
"""
import hashlib
import json
from collections import OrderedDict
from datetime import datetime

import numpy as np
import openai
import pandas as pd

from . import forecast, grounding, knowledge, responses
from .llm import model_name
from .analysis import LOAD_TYPE_KO, PF_HOURS, WEEKDAY_KO, _power_factor
from .data import HOLIDAYS_2018, load_data

SHIFTS = {"A": (6, "06:00", "14:00"), "B": (14, "14:00", "22:00"), "C": (22, "22:00", "06:00")}
SHIFT_HOURS = 8
USUAL_DAYS = 28
WARN_DIFF_PCT = 30.0
BAD_DIFF_PCT = 100.0
SIMILAR_PCT = 10.0
MAX_CACHE = 300
_cache: OrderedDict[tuple, dict] = OrderedDict()

PROMPT = """당신은 철강 공장의 교대 인수인계서를 쓰는 담당자입니다. 아래 '사실'만 보고, 다음 조 작업자가 30초 안에 읽을 수 있게 정리하세요.

규칙:
- summary에는 정확히 3개의 문장을 넣습니다. 각 문장은 50자 안팎입니다.
- items에는 다음 조에 넘길 일을, next에는 다음 조가 시작하자마자 확인할 일을 '사실'의 순서대로 한 문장씩 넣습니다. 개수는 '사실'과 같게 합니다.
- 숫자는 '사실'에 있는 숫자만 그대로 씁니다. 새로 계산하거나 반올림을 바꾸지 마세요. 시각(14:00)도 그대로 씁니다.
- 매뉴얼 표기([SOP-EN-01 2. 경보 단계별 조치])는 바꾸지 말고 그대로 둡니다.
- 원인은 단정하지 말고 '~일 수 있어요'처럼 씁니다. 원화 금액은 쓰지 마세요.
- 존댓말(~예요, ~해 주세요)로, 현장 작업자가 바로 알아듣게 씁니다."""

SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "array", "items": {"type": "string"}},
        "items": {"type": "array", "items": {"type": "string"}},
        "next": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "items", "next"],
    "additionalProperties": False,
}


def _day_type(day: pd.Timestamp) -> int:
    """0 평일, 1 토요일, 2 일요일·공휴일 (경보 모델과 같은 구분)"""
    if day.strftime("%Y-%m-%d") in HOLIDAYS_2018 or day.day_of_week == 6:
        return 2
    return 1 if day.day_of_week == 5 else 0


def window(date: str, shift: str) -> tuple[pd.Timestamp, pd.Timestamp, bool]:
    """교대의 [시작, 끝) 시각과, 데이터가 끝나 일부만 있는지(partial)"""
    if shift not in SHIFTS:
        raise ValueError(f"교대는 {', '.join(SHIFTS)} 중 하나여야 합니다. 입력: {shift}")
    try:
        day = pd.Timestamp(date)
    except ValueError as exc:
        raise ValueError(f"날짜 형식이 잘못되었습니다: {date}. YYYY-MM-DD로 입력하세요.") from exc
    df = load_data()
    first, last = df.index[0].normalize(), df.index[-1].normalize()
    if day != day.normalize() or day < first or day > last:
        raise ValueError(f"데이터 범위는 {first.date()} ~ {last.date()}입니다. 입력: {date}")
    start = day + pd.Timedelta(hours=SHIFTS[shift][0])
    end = start + pd.Timedelta(hours=SHIFT_HOURS)
    data_end = df.index[-1] + pd.Timedelta(minutes=15)
    return start, min(end, data_end), end > data_end


def _usage(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> float:
    return float(df.loc[start : end - pd.Timedelta(minutes=15), "usage_kwh"].sum())


def _usual(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> float | None:
    """지난 4주 중 같은 날 유형(평일/토/일·공휴일)의 같은 조 사용량 중앙값"""
    kind = _day_type(start.normalize())
    values = []
    for back in range(1, USUAL_DAYS + 1):
        s = start - pd.Timedelta(days=back)
        if s < df.index[0] or _day_type(s.normalize()) != kind:
            continue
        values.append(_usage(df, s, s + (end - start)))
    return float(np.median(values)) if values else None


def facts(date: str, shift: str) -> dict:
    """인수인계서에 들어갈 숫자. 모두 데이터와 경보 모델, 대응 기록에서 계산한다."""
    start, end, partial = window(date, shift)
    df = load_data()
    frame = df.loc[start : end - pd.Timedelta(minutes=15)]
    usage = float(frame["usage_kwh"].sum())
    usual = _usual(df, start, end)
    peak_at = frame["peak_kw"].idxmax()
    daytime = frame[(frame.index.hour >= PF_HOURS[0]) & (frame.index.hour < PF_HOURS[1])]
    loads = frame.groupby("load_type")["usage_kwh"].sum()

    alarms = forecast.window_alarms(start, end)
    records = responses.between(f"{start:%Y-%m-%d %H:%M}", f"{end:%Y-%m-%d %H:%M}")
    counts = {key: sum(r["response"] == key for r in records) for key in responses.RESPONSES}
    for target in forecast.TARGETS:
        for item in alarms[target]["경보"]:
            # 경보 구간 안의 어느 시각에든 같은 대상의 응답이 있으면 응답한 것으로 본다.
            answered = [r for r in records if r["target"] == target and item["time"] <= r["time"] < item["end_time"]]
            item["응답"] = responses.RESPONSES[answered[-1]["response"]] if answered else None

    next_hour = None
    last_origin = end - pd.Timedelta(minutes=15)
    try:
        f = forecast.forecast_at(f"{last_origin:%Y-%m-%d %H:%M}")
        next_hour = {
            "까지": f["until"][11:],
            "사용량": {"단계": f["usage"]["label"], "확률_pct": f["usage"]["probability_pct"], "할_일": f["usage"]["action"]},
            "역률": {"단계": f["power_factor"]["label"], "확률_pct": f["power_factor"]["probability_pct"],
                     "볼_시간": f["power_factor"]["watched"], "할_일": f["power_factor"]["action"]},
        }
    except ValueError:
        pass  # 데이터 마지막 1시간은 예측할 수 없다.

    day = start.normalize()
    return {
        "date": date,
        "shift": shift,
        "조": f"{shift}조",
        "시간": f"{SHIFTS[shift][1]}~{SHIFTS[shift][2]}" if not partial else f"{SHIFTS[shift][1]}~{end:%H:%M}",
        "start": f"{start:%Y-%m-%d %H:%M}",
        "end": f"{end:%Y-%m-%d %H:%M}",
        "일부만": partial,
        "요일": WEEKDAY_KO[day.day_of_week],
        "휴일": HOLIDAYS_2018.get(day.strftime("%Y-%m-%d")),
        "사용량_kwh": round(usage, 1),
        "평소_kwh": round(usual, 1) if usual is not None else None,
        "평소_대비_pct": round((usage / usual - 1) * 100, 1) if usual else None,
        "최대_수요전력_kw": round(float(frame["peak_kw"].max()), 1),
        "최대_시각": f"{peak_at:%H:%M}",
        "역률_pct": _power_factor(daytime) if len(daytime) else None,
        "부하별_비중_pct": {LOAD_TYPE_KO[k]: round(float(v) / usage * 100, 1) for k, v in loads.items()} if usage else {},
        "사용량_기준_kw": alarms["limit_kw"],
        "역률_기준_pct": alarms["limit_pct"],
        "경보": {target: alarms[target] for target in forecast.TARGETS},
        "응답": {responses.RESPONSES[k]: n for k, n in counts.items()},
        "다음_조_1시간": next_hour,
    }


def _status(f: dict) -> dict:
    alarms = [a for t in forecast.TARGETS for a in f["경보"][t]["경보"]]
    exceeded = any(f["경보"][t]["초과"] for t in forecast.TARGETS)
    diff = f["평소_대비_pct"]
    if any(a["level"] == "danger" and not a["응답"] for a in alarms) or (diff is not None and abs(diff) >= BAD_DIFF_PCT):
        return {"level": "bad", "label": "확인 필요"}
    if alarms or exceeded or (diff is not None and abs(diff) >= WARN_DIFF_PCT) or (f["역률_pct"] is not None and f["역률_pct"] < f["역률_기준_pct"]):
        return {"level": "warn", "label": "주의"}
    return {"level": "good", "label": "좋음"}


def _manual_refs(f: dict) -> list[dict]:
    refs = []
    for target in forecast.TARGETS:
        if f["경보"][target]["경보"] or f["경보"][target]["초과"]:
            top = knowledge.for_alarm(target, 1)
            if top:
                refs.append({"doc_id": top[0]["doc_id"], "title": top[0]["title"], "section": top[0]["section"], "cite": top[0]["cite"]})
    rest_day = bool(f["휴일"]) or f["요일"] == "일"
    if rest_day and (f["평소_대비_pct"] or 0) >= WARN_DIFF_PCT:
        refs.append({"doc_id": "SOP-OP-03", "title": "휴일·야간 특근 전력 사용 신고", "section": "2. 절차", "cite": "[SOP-OP-03 2. 절차]"})
    return refs


def _rule_note(f: dict, refs: list[dict]) -> dict:
    usage, usual, diff = f["사용량_kwh"], f["평소_kwh"], f["평소_대비_pct"]
    holiday = f"쉬는 날({f['휴일']})이라 " if f["휴일"] else ""
    if diff is None:
        compare = "비교할 평소 기록이 없어요."
    elif diff >= 100:
        compare = f"평소({usual:,.1f}kWh)의 {usage / usual:.1f}배예요."
    elif abs(diff) < SIMILAR_PCT:
        compare = f"평소({usual:,.1f}kWh)와 비슷해요."
    else:
        compare = f"평소({usual:,.1f}kWh)보다 {abs(diff)}% {'많아요' if diff > 0 else '적어요'}."
    pf = f["역률_pct"]
    pf_text = "역률은 09~22시만 봐요." if pf is None else f"역률은 {pf}%였어요."
    alarm_count = {t: len(f["경보"][t]["경보"]) for t in forecast.TARGETS}
    exceed_count = sum(len(f["경보"][t]["초과"]) for t in forecast.TARGETS)
    if sum(alarm_count.values()) or exceed_count:
        alarm_text = f"경보는 사용량 {alarm_count['usage']}번, 역률 {alarm_count['power_factor']}번 켜졌고, 실제로 기준을 넘은 때는 {exceed_count}번이에요."
    else:
        alarm_text = "경보는 한 번도 켜지지 않았고, 기준을 넘은 때도 없어요."
    summary = [
        f"{holiday}{f['조']}({f['시간']}) 동안 전기를 {usage:,.1f}kWh 썼어요. {compare}",
        f"가장 많이 쓴 때는 {f['최대_시각']}({f['최대_수요전력_kw']:,.1f}kW)이고, {pf_text}",
        alarm_text,
    ]

    items = []
    for target in forecast.TARGETS:
        name = f["경보"][target]["이름"]
        open_alarms = [a for a in f["경보"][target]["경보"] if not a["응답"]]
        if open_alarms:
            worst = "위험" if any(a["level"] == "danger" for a in open_alarms) else "주의"
            times = ", ".join(a["시작"] for a in open_alarms)
            items.append(f"{name} 경보 {len(open_alarms)}번({times}, 가장 높은 단계 {worst})에 응답이 없어요. 그 시각 상황을 확인하고 응답을 남겨 주세요.")
        exceed = f["경보"][target]["초과"]
        if exceed:
            times = ", ".join(e["시작"] for e in exceed)
            if target == "usage":
                value = f"최고 {max(e['최고_kw'] for e in exceed):,.1f}kW"
            else:
                value = f"최저 {min(e['최저_pct'] for e in exceed)}%"
            caught = [e["몇분_먼저"] for e in exceed if e["몇분_먼저"] is not None]
            if len(caught) == len(exceed):
                lead = f"모두 경보가 먼저 알렸어요(가장 늦게 알린 것도 {min(caught)}분 전)"
            elif caught:
                lead = f"그중 {len(exceed) - len(caught)}번은 미리 알리지 못했어요"
            else:
                lead = "미리 알리지 못했어요"
            items.append(f"{name}이 {len(exceed)}번 기준을 넘었어요({times}, {value}). {lead}. 원인을 작업일지에 적어 주세요.")
    if diff is not None and diff >= WARN_DIFF_PCT:
        amount = f"평소의 {usage / usual:.1f}배를" if diff >= 100 else f"평소보다 {diff}% 많이"
        items.append(f"{amount} 썼어요. 특근·시운전 같은 작업 변경이 있었을 수 있어요. 작업일지를 확인해 주세요.")
    elif diff is not None and diff <= -WARN_DIFF_PCT and not f["휴일"]:
        items.append(f"평소보다 {abs(diff)}% 적게 썼어요. 설비가 멈춰 있었을 수 있어요. 가동 기록을 확인해 주세요.")
    if pf is not None and pf < f["역률_기준_pct"]:
        items.append(f"역률이 {pf}%로 기준({f['역률_기준_pct']}%)보다 낮았어요. 콘덴서 단계를 확인해 주세요.")
    if not items:
        items.append("특별히 넘길 일은 없어요.")

    next_checks = []
    n = f["다음_조_1시간"]
    if n:
        next_checks.append(f"다음 조 시작 1시간({n['까지']}까지) 사용량 경보는 {n['사용량']['단계']}({n['사용량']['확률_pct']}%)예요. {n['사용량']['할_일']}")
        if n["역률"]["볼_시간"]:
            next_checks.append(f"같은 시간 역률 경보는 {n['역률']['단계']}({n['역률']['확률_pct']}%)예요. {n['역률']['할_일']}")
    else:
        next_checks.append("데이터의 마지막이라 다음 조 경보를 예측할 수 없어요.")
    for ref in refs:
        next_checks.append(f"참고 매뉴얼: {ref['cite']} {ref['title']}")
    return {"summary": summary, "items": items, "next": next_checks}


def _ai_note(client: openai.OpenAI, f: dict, note: dict, status: dict) -> dict | None:
    given = {"상태": status["label"], "요약": note["summary"], "인계_사항": note["items"], "다음_조": note["next"]}
    response = client.chat.completions.create(
        model=model_name(),
        max_completion_tokens=4000,
        messages=[
            {"role": "system", "content": PROMPT},
            {"role": "user", "content": "사실:\n" + json.dumps(given, ensure_ascii=False, indent=1)},
        ],
        response_format={"type": "json_schema", "json_schema": {"name": "handover", "strict": True, "schema": SCHEMA}},
    )
    try:
        body = json.loads(response.choices[0].message.content or "")
    except json.JSONDecodeError:
        return None
    out = {}
    for key, expected in (("summary", 3), ("items", len(note["items"])), ("next", len(note["next"]))):
        value = body.get(key)
        if not isinstance(value, list) or len(value) != expected or not all(isinstance(x, str) and x.strip() for x in value):
            return None
        out[key] = [x.strip() for x in value]
    text = " ".join(out["summary"] + out["items"] + out["next"])
    if not grounding.check(text, given, strict=True)["ok"]:
        return None
    # 매뉴얼 출처 표기가 빠지면 작업자가 문서를 찾을 수 없으므로 버린다.
    if not all(line.split(" ")[2] in text for line in note["next"] if line.startswith("참고 매뉴얼: ")):
        return None
    return out


def markdown(h: dict) -> str:
    f = h["facts"]
    holiday = f" · {f['휴일']}" if f["휴일"] else ""
    usual = f"평소 {f['평소_kwh']:,.1f} kWh, {f['평소_대비_pct']:+}%" if f["평소_kwh"] is not None else "평소 기록 없음"
    alarms = ", ".join(
        f"{f['경보'][t]['이름']} {len(f['경보'][t]['경보'])}번 (실제 초과 {len(f['경보'][t]['초과'])}번)" for t in forecast.TARGETS
    )
    answered = ", ".join(f"{k} {v}" for k, v in f["응답"].items())
    unanswered = sum(1 for t in forecast.TARGETS for a in f["경보"][t]["경보"] if not a["응답"])
    source = "AI가 문장을 다듬음 (숫자 검증 통과)" if h["source"] == "ai" else "계산 결과로 자동 작성"
    lines = [
        f"# 교대 인수인계서: {f['date']} ({f['요일']}{holiday}) {f['조']} {f['시간']}",
        "",
        f"상태: **{h['status']['label']}** · {source} · 작성 {h['created_at']}",
        "",
        "## 요약",
        *[f"- {x}" for x in h["note"]["summary"]],
        "",
        "## 인계 사항",
        *[f"- {x}" for x in h["note"]["items"]],
        "",
        "## 다음 조 확인 사항",
        *[f"- {x}" for x in h["note"]["next"]],
        "",
        "## 근거 숫자",
        "",
        "| 항목 | 값 |",
        "|---|---|",
        f"| 사용량 | {f['사용량_kwh']:,.1f} kWh ({usual}) |",
        f"| 최대 수요전력 | {f['최대_수요전력_kw']:,.1f} kW ({f['최대_시각']}) |",
        f"| 역률 (09~22시) | {f['역률_pct'] if f['역률_pct'] is not None else '보지 않는 시간'}{'%' if f['역률_pct'] is not None else ''} |",
        f"| 경보 | {alarms} |",
        f"| 작업자 응답 | {answered}, 미응답 경보 {unanswered} |",
        "",
        "---",
        "2018년 공개 데이터(UCI Steel Industry Energy Consumption)로 자동 작성한 시연용 인수인계서입니다. 경보는 알리기만 하고 설비를 제어하지 않습니다.",
    ]
    if f["일부만"]:
        lines.insert(3, "> 데이터가 2018-12-31 24:00에서 끝나 이 교대는 일부만 들어 있습니다.\n")
    return "\n".join(lines)


def handover(date: str, shift: str, client: openai.OpenAI | None) -> dict:
    f = facts(date, shift)
    status = _status(f)
    refs = _manual_refs(f)
    note = _rule_note(f, refs)
    result = {
        "date": date, "shift": shift, "status": status, "facts": f, "note": note, "manual_refs": refs,
        "source": "rule", "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    # 응답 기록이 바뀌면 사실도 바뀌므로, 사실 전체의 요약값을 캐시 키에 넣는다.
    key = (date, shift, model_name(), hashlib.sha1(json.dumps(note, ensure_ascii=False).encode()).hexdigest())
    if key in _cache:
        _cache.move_to_end(key)
        result = {**result, "note": _cache[key], "source": "ai"}
    elif client is not None:
        try:
            ai = _ai_note(client, f, note, status)
        except openai.OpenAIError:
            ai = None
        if ai is not None:
            _cache[key] = ai
            while len(_cache) > MAX_CACHE:
                _cache.popitem(last=False)
            result = {**result, "note": ai, "source": "ai"}
    result["markdown"] = markdown(result)
    return result


def agent_handover(date: str, shift: str) -> dict:
    """에이전트 도구용: AI를 다시 부르지 않고 사실과 코드가 쓴 인수인계 내용을 준다."""
    h = handover(date, shift, None)
    return {"상태": h["status"]["label"], **h["note"], "근거": h["facts"], "참고_매뉴얼": [r["cite"] for r in h["manual_refs"]]}
