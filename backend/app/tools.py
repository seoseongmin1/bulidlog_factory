"""모델에게 공개하는 도구 정의와 실행기."""
import json

from . import analysis, forecast, handover, knowledge

DATE = {"type": "string", "description": "YYYY-MM-DD 형식의 날짜. 2018-01-01 ~ 2018-12-31"}
METRIC = {"type": "string", "enum": list(analysis.METRICS), "description": "분석할 지표"}


def _tool(name: str, description: str, properties: dict) -> dict:
    return {
        "name": name,
        "description": description,
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        },
    }


TOOLS = [
    _tool("get_dataset_info", "데이터의 출처, 기간, 지표, 그리고 데이터에 없는 정보를 확인한다.", {}),
    _tool(
        "summarize_period",
        "기간의 총 사용량, 일평균, CO2, 최대 수요전력과 그 시각, 역률, 부하별 비중을 구한다.",
        {"start": DATE, "end": DATE},
    ),
    _tool(
        "get_timeseries",
        "기간의 지표를 시간, 일, 주, 월 단위로 집계한다. 결과는 화면에 차트로 표시된다. 최대 400행까지 가능하다.",
        {
            "start": DATE,
            "end": DATE,
            "freq": {"type": "string", "enum": ["hour", "day", "week", "month"], "description": "집계 단위"},
            "metric": METRIC,
        },
    ),
    _tool(
        "get_profile",
        "기간의 지표를 시간대, 요일, 부하 유형별로 묶어 패턴을 본다. 사용량과 CO2는 일평균이다. 결과는 차트로 표시된다.",
        {
            "start": DATE,
            "end": DATE,
            "by": {"type": "string", "enum": ["hour_of_day", "weekday", "load_type"], "description": "묶는 기준"},
            "metric": METRIC,
        },
    ),
    _tool(
        "compare_periods",
        "두 기간(A, B)의 일평균 사용량, 최대 수요전력, 역률을 비교한다.",
        {"a_start": DATE, "a_end": DATE, "b_start": DATE, "b_end": DATE},
    ),
    _tool(
        "detect_anomalies",
        "주변 같은 요일 유형과 비교해 사용량이 비정상인 날을 찾고, 공휴일 여부를 함께 알려준다.",
        {
            "start": DATE,
            "end": DATE,
            "top_n": {"type": "integer", "description": "반환할 최대 개수. 보통 10"},
            "min_score": {"type": "number", "description": "강건 Z-점수 기준. 보통 3.0"},
        },
    ),
    _tool(
        "analyze_power_factor",
        "기간의 지상 역률과 기준 미달 비율, 역률이 낮은 시간대를 분석한다. 결과는 차트로 표시된다.",
        {"start": DATE, "end": DATE, "threshold": {"type": "number", "description": "역률 기준(%). 보통 90"}},
    ),
    _tool(
        "simulate_peak_cap",
        "최대 수요전력을 일정 비율 낮출 때 옮겨야 할 사용량과 하루 안에 옮길 수 있는 일수를 계산한다.",
        {"start": DATE, "end": DATE, "reduction_pct": {"type": "number", "description": "낮출 비율(%)"}},
    ),
    _tool(
        "get_daily_report",
        "특정 날짜의 일일 보고서 수치를 만든다. 결과는 차트로 표시된다.",
        {"date": DATE},
    ),
    _tool(
        "plan_power_factor_correction",
        "기간의 09~22시 역률을 목표까지 올리는 데 필요한 콘덴서 용량(kVar)을 계산한다. 자동 역률 조정기 추천 용량과, 같은 용량의 "
        "고정 콘덴서를 달았을 때(과보상 포함)를 비교한다. 시간대별 평균 부족 kVar는 차트로 표시된다. 금액은 계산하지 않는다.",
        {"start": DATE, "end": DATE, "target_pct": {"type": "number", "description": "목표 역률(%). 80~99, 보통 90"}},
    ),
    _tool(
        "search_manual",
        "현장 매뉴얼(예시 문서: 경보 대응 절차, 콘덴서 점검, 대형 설비 기동 순서, 교대 인수인계 기준, 경보 응답 기준, 특근 신고, 용어 설명)에서 "
        "질문과 관련된 절을 찾는다. 무엇을 해야 하는지, 절차, 규정, 용어를 물으면 쓴다. 결과의 cite 표기로 출처를 밝힌다.",
        {"query": {"type": "string", "description": "찾을 내용. 예: 역률 경보가 켜지면 할 일"}},
    ),
    _tool(
        "get_shift_handover",
        "교대(A조 06~14시, B조 14~22시, C조 22시~다음 날 06시)의 인수인계 내용을 만든다. 그 조의 사용량과 평소 대비, 켜진 경보와 "
        "실제 초과, 작업자 응답, 미응답 경보, 다음 조 시작 1시간 경보, 참고 매뉴얼을 준다.",
        {"date": DATE, "shift": {"type": "string", "enum": list(handover.SHIFTS), "description": "교대 조"}},
    ),
    _tool(
        "forecast_alarm",
        "고른 시각을 '지금'으로 보고 앞으로 1시간 안에 사용량(15분 수요전력)이나 역률(09~22시, 85%)이 기준을 넘을 확률과 "
        "좋음·주의·위험, 할 일을 머신러닝 모델로 예측한다. 같은 모델의 9~12월 채점 결과도 준다.",
        {"time": {"type": "string", "description": "YYYY-MM-DD HH:MM, 15분 단위. 2018-01-01 00:00 ~ 2018-12-31 22:45"}},
    ),
    _tool(
        "get_alarm_timeline",
        "하루 동안 경보가 켜진 구간, 실제로 기준을 넘은 구간, 그날 경보가 몇 분 먼저 울렸는지를 구한다. 15분 수요전력은 차트로 표시된다.",
        {"date": DATE},
    ),
    _tool(
        "summarize_alarms",
        "기간의 경보 횟수, 헛경보, 실제로 기준을 넘은 횟수와 미리 알린 비율을 구한다. 시간대별 사용량 경보 칸 수는 차트로 표시된다.",
        {"start": DATE, "end": DATE},
    ),
]

def openai_tools() -> list[dict]:
    """같은 도구 정의를 OpenAI의 함수 호출 형식으로 바꾼다."""
    return [
        {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool["description"],
                "strict": True,
                "parameters": tool["input_schema"],
            },
        }
        for tool in TOOLS
    ]


_HANDLERS = {
    "get_dataset_info": analysis.dataset_info,
    "summarize_period": analysis.summarize,
    "get_timeseries": analysis.timeseries,
    "get_profile": analysis.profile,
    "compare_periods": analysis.compare_periods,
    "detect_anomalies": analysis.detect_anomalies,
    "analyze_power_factor": analysis.power_factor_report,
    "simulate_peak_cap": analysis.simulate_peak_cap,
    "get_daily_report": analysis.daily_report,
    "plan_power_factor_correction": analysis.plan_power_factor_correction,
    "search_manual": lambda query: knowledge.search(query, 3),
    "get_shift_handover": handover.agent_handover,
    "forecast_alarm": forecast.agent_forecast,
    "get_alarm_timeline": forecast.agent_timeline,
    "summarize_alarms": forecast.summarize_alarms,
}


def _chart_for(name: str, args: dict, result: dict) -> dict | None:
    """차트는 도구 결과에서만 만든다. 모델이 쓴 숫자는 그리지 않는다."""
    rows = result.get("rows")
    if not rows or len(rows) < 2:
        return None
    if name == "summarize_alarms":
        return {"title": f"시간대별 사용량 경보 칸 수 ({args['start']} ~ {args['end']})", "unit": "칸", "kind": "bar", "rows": rows}
    if name == "plan_power_factor_correction":
        return {
            "title": f"시간대별 평균 부족 무효전력 (목표 {args['target_pct']}%, {args['start']} ~ {args['end']})",
            "unit": "kVar",
            "kind": "bar",
            "rows": rows,
        }
    if name == "get_alarm_timeline":
        return {"title": f"15분 수요전력 ({args['date']}, 기준 {result['사용량_기준_kw']}kW)", "unit": "kW", "kind": "line", "rows": rows}
    metric = result.get("metric", "power_factor" if name == "analyze_power_factor" else "usage_kwh")
    label, unit = analysis.METRIC_LABELS[metric]
    scope = args.get("date") or f"{args['start']} ~ {args['end']}"
    continuous = name in ("get_timeseries", "get_daily_report", "analyze_power_factor") or args.get("by") == "hour_of_day"
    return {
        "title": f"{label} ({scope})",
        "unit": unit,
        "kind": "line" if continuous and len(rows) > 12 else "bar",
        "rows": rows,
    }


def run_tool(name: str, args: dict) -> tuple[str, dict | None, bool]:
    """결과 JSON, 차트, 오류 여부를 돌려준다. 오류는 모델이 고칠 수 있도록 메시지로 전달한다."""
    handler = _HANDLERS.get(name)
    if handler is None:
        return f"알 수 없는 도구입니다: {name}", None, True
    try:
        result = handler(**args)
    except (ValueError, TypeError, KeyError) as exc:
        return str(exc), None, True
    return json.dumps(result, ensure_ascii=False), _chart_for(name, args, result), False
