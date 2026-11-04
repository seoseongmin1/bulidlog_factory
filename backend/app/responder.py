"""조치 에이전트(LLM). 경보 사건을 조사하고, 조치를 시뮬레이션해 보고, 제안한다. 실행·승인·검증은 actions.py가 한다.

에이전트가 쓸 수 있는 도구는 5개뿐이다: get_incident, simulate_action, propose_action, search_manual, finish.
- 실제 미래 값은 주지 않는다. 시뮬레이션은 예상값 위에서만 한다.
- 위험도 낮은 조치는 제안과 동시에 실행되고, 설비 조치는 작업자 승인을 기다린다(코드가 강제한다).
- 승인된 조치를 실행해 실제 기록으로 검증한 결과가 '해결 안 됨'이면, 그 결과를 에이전트에게 다시 주고 다음 조치를 계획하게 한다.
API 키가 없거나 AI가 실패하면 같은 안전장치를 쓰는 규칙 계획자(actions.rule_plan)가 대신 계획한다.
"""
import json

import openai

from . import actions, knowledge
from .llm import model_name

MAX_ROUNDS = 10


class AgentFailure(RuntimeError):
    """모델이 읽을 수 없는 응답을 줬을 때. 규칙 계획자로 바꾼다."""

MAX_REPLANS = 2

SYSTEM_PROMPT = """당신은 철강 공장의 전력 경보에 대응하는 조치 에이전트입니다. 경보 사건 하나를 맡아, 다음 2시간 동안 사용량(15분 수요전력)과 역률이 기준을 넘지 않게 하는 조치를 계획합니다.

일하는 순서:
1. get_incident로 경보, 예상값, 불확실성, 조치 목록과 한도를 확인합니다.
2. 필요하면 search_manual로 현장 절차를 확인합니다.
3. 설비 조치는 반드시 simulate_action으로 먼저 효과를 봅니다. '보수적_최대_kw'(예상 최대 + 불확실성)가 앞 1시간과 다음 1시간 모두 기준 아래가 되는지 확인하세요.
4. propose_action으로 조치를 제안합니다. 위험도 낮은 조치(notify, create_work_order)는 바로 실행되고, 설비 조치는 작업자 승인을 기다립니다.
5. finish로 무엇을 왜 했는지, 무엇은 할 수 없어서 사람에게 넘겼는지를 3~5문장으로 정리합니다.

판단 원칙:
- 이 공장 데이터로 채점해 보니, '큰 설비 기동 미루기'(defer_load)는 다음 시간에도 부하가 높으면 미룬 부하가 다시 기준을 넘겨 오히려 나빠졌습니다. 다음 1시간에 여유가 있을 때만 미루고, 아니면 보조 설비 정지(shed_auxiliary)를 쓰세요.
- 콘덴서(capacitor_step)를 너무 많이, 너무 오래 넣으면 부하가 가벼운 칸에서 과보상(진상)이 됩니다. 시뮬레이션의 lead_slots가 0인지 확인하세요.
- 조치 한도로 해결할 수 없으면 무리하지 말고 notify로 에너지 담당이나 생산관리에게 판단을 넘기세요. 생산 일정은 사람이 정합니다.
- 경보 단계와 관계없이 설비 조치를 하면 notify로 현장 반장에게 무엇을 언제 하는지 알리세요.
- 숫자는 도구 결과에서만 가져오고, 매뉴얼을 따른 조치에는 cite 표기([SOP-EN-01 2. 경보 단계별 조치])를 이유에 붙이세요.
- 이 시스템의 설비 조치는 가상 공장(2018년 기록 위의 시뮬레이터)에만 적용됩니다.

한국어로 쓰고, 같은 조치를 반복해서 제안하지 마세요."""


def _tool(name: str, description: str, properties: dict, required: list[str] | None = None) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required if required is not None else list(properties),
                           "additionalProperties": False},
        },
    }


ACTION_TYPE = {"type": "string", "enum": list(actions.ACTIONS), "description": "조치 종류"}
PARAMS = {
    "type": "object",
    "description": "조치 입력. defer_load/shed_auxiliary: {start:'HH:MM', minutes, kw}, capacitor_step: {start, minutes, steps}, "
                   "notify: {to, message}, create_work_order: {assignee, title, due_minutes, cite}",
}
TOOLS = [
    _tool("get_incident", "사건의 경보, 앞으로 2시간 예상값, 불확실성, 조치 목록·한도·위험도, 이미 한 조치를 본다.", {}),
    _tool("simulate_action", "설비 조치의 효과를 예상값 위에서 미리 계산한다. 아무것도 실행하지 않는다.",
          {"action_type": {"type": "string", "enum": list(actions.PLANT_ACTIONS)}, "params": PARAMS}),
    _tool("propose_action", "조치를 제안한다. 위험도 낮은 조치는 바로 실행되고, 설비 조치는 작업자 승인을 기다린다.",
          {"action_type": ACTION_TYPE, "params": PARAMS, "reason": {"type": "string", "description": "왜 이 조치인지 한두 문장"}}),
    _tool("search_manual", "현장 매뉴얼에서 절차를 찾는다.", {"query": {"type": "string"}}),
    _tool("finish", "계획을 마치고 요약을 남긴다.", {"summary": {"type": "string"}}),
]


def _run_tool(incident: dict, name: str, args: dict) -> tuple[str, bool]:
    try:
        if name == "get_incident":
            result = actions.overview(incident)
        elif name == "simulate_action":
            result = actions.simulate(incident, args.get("action_type"), args.get("params") or {})
        elif name == "propose_action":
            result = actions.propose(incident, args.get("action_type"), args.get("params") or {}, args.get("reason", ""), "agent")
        elif name == "search_manual":
            result = knowledge.search(args.get("query", ""), 3)
        else:
            return f"알 수 없는 도구입니다: {name}", True
    except (ValueError, KeyError, TypeError) as exc:
        actions._log(incident, "system", "blocked", f"{name} 거부: {exc}")
        return str(exc), True
    return json.dumps(result, ensure_ascii=False), False


def _loop(client: openai.OpenAI, incident: dict, messages: list) -> bool:
    """도구 호출 루프. finish를 부르거나 답을 끝내면 True."""
    for _ in range(MAX_ROUNDS):
        response = client.chat.completions.create(
            model=model_name(), max_completion_tokens=8000,
            messages=[{"role": "system", "content": SYSTEM_PROMPT}, *messages], tools=TOOLS,
        )
        if not getattr(response, "choices", None):
            raise AgentFailure("모델 응답에 내용이 없습니다.")
        message = response.choices[0].message
        if message.content and message.content.strip():
            actions._log(incident, "agent", "think", message.content.strip()[:600])
        calls = message.tool_calls or []
        if not calls:
            incident["summary"] = (message.content or "").strip() or incident["summary"]
            return True
        messages.append({"role": "assistant", "content": message.content, "tool_calls": [
            {"id": c.id, "type": "function", "function": {"name": c.function.name, "arguments": c.function.arguments}} for c in calls]})
        done = False
        for call in calls:
            try:
                args = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                args = None
            if not isinstance(args, dict):
                content, error = "도구 입력이 올바른 JSON이 아닙니다.", True
            elif call.function.name == "finish":
                incident["summary"] = str(args.get("summary", "")).strip()[:1000]
                actions._log(incident, "agent", "finish", incident["summary"] or "계획을 마쳤어요.")
                content, error, done = "기록했습니다.", False, True
            else:
                content, error = _run_tool(incident, call.function.name, args)
            messages.append({"role": "tool", "tool_call_id": call.id, "content": f"오류: {content}" if error else content})
        if done:
            return True
    actions._log(incident, "system", "blocked", f"도구를 {MAX_ROUNDS}번 불러도 계획을 끝내지 못했어요.")
    return False


def _finish_status(incident: dict) -> None:
    if not any(a["status"] == "pending" for a in incident["actions"]):
        actions.verify(incident)
    actions._refresh_status(incident)


def respond(time: str, client: openai.OpenAI | None) -> dict:
    """경보 시각 하나에 대해 사건을 열고 조치를 계획한다."""
    incident = actions.open_incident(time)
    if not incident["targets"]:
        return incident
    if client is not None:
        incident["planner"] = "ai"
        incident["_messages"] = [{"role": "user", "content": f"{incident['time']} 경보 사건에 대응해 주세요."}]
        try:
            if _loop(client, incident, incident["_messages"]):
                _finish_status(incident)
                return incident
        except (openai.OpenAIError, AgentFailure) as exc:
            actions._log(incident, "system", "fallback", f"AI 호출 실패({type(exc).__name__})로 규칙 계획자로 바꿔요.")
        if incident["actions"]:
            # AI가 이미 낸 조치는 그대로 두고(낮은 위험은 실행됐다) 상태만 정리한다. 규칙 계획을 겹쳐 내지 않는다.
            _finish_status(incident)
            return incident
        incident["planner"] = "rule (AI 실패 후 대체)"
    actions.rule_plan(incident)
    return incident


def after_decision(incident: dict, client: openai.OpenAI | None) -> dict:
    """승인·거절 뒤: 대기 중인 조치가 없고 실제 검증이 '해결 안 됨'이면 에이전트가 한 번 더 계획한다(최대 MAX_REPLANS번)."""
    if any(a["status"] == "pending" for a in incident["actions"]):
        return incident
    result = actions.verify(incident)
    actions._refresh_status(incident)
    replans = incident.setdefault("_replans", 0)
    if result["resolved"] or replans >= MAX_REPLANS or len(incident["actions"]) >= actions.MAX_ACTIONS:
        return incident
    incident["_replans"] += 1
    if client is not None and incident.get("_messages") is not None:
        rejected = [a["id"] for a in incident["actions"] if a["status"] == "rejected"]
        keys = ("peak_kw", "over_slots", "min_pf", "pf_under_slots", "lead_slots")
        observed = {"조치_후_실제": {k: result["with"][k] for k in keys}, "판정": result["checks"], "거절된_조치": rejected}
        incident["_messages"].append({"role": "user", "content": "승인된 조치를 실행하고 실제 기록으로 확인했는데 아직 해결되지 않았습니다. "
                                      "거절된 조치는 다시 제안하지 말고, 남은 한도 안에서 추가 조치를 하거나 사람에게 넘기세요.\n"
                                      + json.dumps(observed, ensure_ascii=False)})
        try:
            _loop(client, incident, incident["_messages"])
        except (openai.OpenAIError, AgentFailure):
            actions._log(incident, "system", "fallback", "AI 호출 실패로 추가 계획을 하지 못했어요.")
    elif not any(a["type"] == "notify" and a["params"]["to"] == "에너지 담당" for a in incident["actions"]):
        # 규칙 계획자는 더 할 수 있는 조치가 없으면 사람에게 넘긴다.
        actions.propose(incident, "notify", {"to": "에너지 담당", "message": f"{incident['time']} 경보: 실행한 조치로는 기준을 지키지 못했어요. "
                                             "추가 판단(생산 일정 조정 등)이 필요해요."}, "에이전트의 조치 한도를 넘어 사람에게 넘깁니다.", "agent")
    _finish_status(incident)
    return incident
