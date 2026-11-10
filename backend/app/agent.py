"""OpenAI 도구 호출 루프. 진행 상황을 화면에 보내기 위해 이벤트를 하나씩 내보낸다."""
import json
from collections.abc import Iterator

from openai import OpenAI

from . import grounding
from .llm import DEFAULT_MODEL, model_name  # noqa: F401  (테스트와 다른 모듈이 여기서 가져간다)
from .tools import openai_tools, run_tool

MAX_TOOL_ROUNDS = 12

SYSTEM_PROMPT = """당신은 철강 공장의 에너지 데이터를 분석하는 에이전트입니다. 생산관리자와 에너지 담당자가 사용합니다.

분석 대상은 대우스틸 광양공장의 2018년 전력 데이터(15분 간격)입니다. 실시간 데이터가 아니므로 "어제", "지난주" 같은 표현은 데이터의 마지막 날인 2018-12-31을 기준으로 해석하고, 해석한 날짜를 답변에 밝히세요.

답변 원칙:
- 모든 수치는 도구 결과에서 가져오세요. 기억이나 추정으로 수치를 쓰지 말고, 도구 결과를 비율로 환산했다면 계산임을 밝히세요.
- 데이터에는 설비별 전력, 생산량, 제품, 기상, 전기요금이 없습니다. 그래서 전력 패턴은 사실로 말할 수 있지만 원인은 확정할 수 없습니다. 도구로 확인한 사실과 가설을 구분해서 쓰고, 가설에는 "가능성"이라고 표시하며 확인할 방법을 함께 제시하세요.
- 원화 금액은 제시하지 마세요. 요금을 물으면 요금 정보가 데이터에 없다고 말하세요.
- 2018년 밖의 기간이나 공장 전력과 관련 없는 주제처럼 데이터로 답할 수 없는 질문은, 답할 수 없는 이유를 설명하고 답할 수 있는 질문의 예를 알려 주세요. 숫자를 지어내지 마세요.
- 설비를 끄거나 켜는 것처럼 제어를 요청받으면, 이 서비스는 데이터를 읽고 분석만 한다고 설명하고 제어하지 마세요. 분석으로 도울 수 있는 부분을 제안하세요.
- 도구 결과의 차트는 화면에 자동으로 표시되므로, 값을 표로 다시 나열하지 말고 핵심과 의미를 설명하세요.
- 도구가 오류를 반환하면 입력을 고쳐 다시 시도하고, 그래도 안 되면 무엇을 확인하지 못했는지 말하세요.
- 경보 예측(forecast_alarm, get_alarm_timeline, summarize_alarms)은 2018년의 한 시각을 "지금"으로 본 머신러닝 확률 예측입니다. 예측을 사실처럼 단정하지 말고 확률과 위험 정도로 말하며, 헛경보가 있다는 점과 채점 결과를 함께 알려 주세요. 1~8월은 학습에 쓴 기간이라 실제보다 잘 맞아 보일 수 있다고 밝히세요. 예측 숫자를 직접 만들지 말고 도구를 쓰세요.
- 내일 예측(forecast_day_ahead)은 하루 전까지의 데이터로 본 위험 시간대입니다. 확률이 아니라 지난 4주 같은 날 유형에서 기준을 넘은 날의 비율로 정한 것이며, 켜진 구간 중 실제로 넘은 비율이 낮아 헛경보가 많다는 점과 채점 결과를 함께 알려 주세요. 특정 시각에 넘는다고 단정하지 마세요.
- 경보 대응, 점검, 설비 기동 순서, 인수인계, 신고처럼 '무엇을 해야 하는지'를 물으면 search_manual로 현장 매뉴얼을 찾아 그 내용으로 답하세요. 매뉴얼 내용을 쓴 문장 끝에는 결과의 cite 표기(예: [SOP-EN-02 2. 경보가 켜지면])를 그대로 붙이세요. 매뉴얼에 없는 절차는 지어내지 말고 매뉴얼에 없다고 말하세요. 처음 인용할 때 시연용 예시 문서라는 점을 한 번 밝히세요.
- 교대나 인수인계를 물으면 get_shift_handover를 쓰세요. "오늘 오후조"처럼 날짜가 없으면 2018-12-31을 기준으로 해석하고 밝히세요.

한국어로 답하고, 결론을 먼저 쓴 뒤 근거를 짧게 설명하세요. 필요한 경우 마지막에 현장에서 취할 수 있는 조치를 제안하세요."""


def verify(text: str, messages: list) -> dict:
    """답변의 숫자를 이 대화의 도구 결과와 사용자 질문에서 찾는다. 못 찾은 숫자는 화면에 표시한다."""
    sources = [m["content"] for m in messages if m["role"] in ("tool", "user") and isinstance(m.get("content"), str)]
    return grounding.check(text, *sources)


def run_turn(client: OpenAI, history: list, user_text: str) -> Iterator[dict]:
    """질문 하나를 처리한다. 성공한 턴만 history에 반영해 대화 상태가 깨지지 않게 한다."""
    messages = [*history, {"role": "user", "content": user_text}]
    tools = openai_tools()

    for _ in range(MAX_TOOL_ROUNDS):
        response = client.chat.completions.create(
            model=model_name(),
            max_completion_tokens=16000,
            messages=[{"role": "system", "content": SYSTEM_PROMPT}, *messages],
            tools=tools,
        )
        choice = response.choices[0]
        message = choice.message

        if choice.finish_reason == "content_filter" or getattr(message, "refusal", None):
            yield {"type": "error", "message": "모델이 이 요청을 처리하지 않았습니다. 질문을 바꿔 다시 시도해 주세요."}
            return

        tool_calls = message.tool_calls or []
        if not tool_calls:
            text = (message.content or "").strip()
            if choice.finish_reason == "length":
                text += "\n\n(답변이 길이 제한으로 잘렸습니다.)"
            messages.append({"role": "assistant", "content": text})
            history[:] = messages
            yield {"type": "answer", "text": text}
            yield {"type": "verification", **verify(text, messages)}
            return

        messages.append(
            {
                "role": "assistant",
                "content": message.content,
                "tool_calls": [
                    {"id": call.id, "type": "function", "function": {"name": call.function.name, "arguments": call.function.arguments}}
                    for call in tool_calls
                ],
            }
        )
        for call in tool_calls:
            try:
                args = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                args = None
            yield {"type": "tool_call", "name": call.function.name, "input": args}
            if not isinstance(args, dict):
                content, chart, is_error = "도구 입력이 올바른 JSON이 아닙니다.", None, True
            else:
                content, chart, is_error = run_tool(call.function.name, args)
            yield {"type": "tool_result", "name": call.function.name, "is_error": is_error}
            if chart:
                yield {"type": "chart", "chart": chart}
            # OpenAI의 도구 결과에는 오류 표시 칸이 없어, 오류일 때는 내용 앞에 밝힌다.
            messages.append({"role": "tool", "tool_call_id": call.id, "content": f"오류: {content}" if is_error else content})

    yield {"type": "error", "message": f"도구를 {MAX_TOOL_ROUNDS}번 호출해도 답을 끝내지 못했습니다. 질문을 좁혀 주세요."}
