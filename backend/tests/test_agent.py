"""가짜 Chat Completions 서버로 실제 SDK를 통과시켜 에이전트 루프를 검증한다."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import openai
import pytest

from app.agent import DEFAULT_MODEL, MAX_TOOL_ROUNDS, run_turn


def completion(message, finish_reason):
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 0,
        "model": DEFAULT_MODEL,
        "choices": [{"index": 0, "message": {"role": "assistant", **message}, "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def tool_call(call_id, start, end):
    arguments = json.dumps({"start": start, "end": end})
    return completion(
        {
            "content": None,
            "tool_calls": [{"id": call_id, "type": "function", "function": {"name": "summarize_period", "arguments": arguments}}],
        },
        "tool_calls",
    )


TOOL_CALL = tool_call("call_1", "2018-01-01", "2018-01-31")
BAD_TOOL_CALL = tool_call("call_2", "2019-01-01", "2019-01-02")
ANSWER = completion({"content": "1월 사용량은 많았습니다."}, "stop")


@pytest.fixture
def api(monkeypatch):
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    state = {"responses": [], "requests": [], "paths": []}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["content-length"])))
            state["requests"].append(body)
            state["paths"].append(self.path)
            reply = state["responses"].pop(0) if len(state["responses"]) > 1 else state["responses"][0]
            payload = json.dumps(reply).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    state["client"] = openai.OpenAI(api_key="test", base_url=f"http://127.0.0.1:{server.server_port}/v1", max_retries=0)
    yield state
    server.shutdown()


def test_tool_result_goes_back_to_model_and_answer_is_returned(api):
    api["responses"] = [TOOL_CALL, ANSWER]
    history = []
    events = list(run_turn(api["client"], history, "1월 요약해줘"))

    assert [e["type"] for e in events] == ["tool_call", "tool_result", "answer", "verification"]
    assert events[-2]["text"] == "1월 사용량은 많았습니다."

    result = api["requests"][1]["messages"][-1]
    assert result["role"] == "tool" and result["tool_call_id"] == "call_1"
    assert json.loads(result["content"])["usage_kwh"] == pytest.approx(126238, abs=1)
    assert len(history) == 4


def test_request_uses_expected_model_settings(api):
    api["responses"] = [ANSWER]
    list(run_turn(api["client"], [], "안녕"))
    body = api["requests"][0]
    assert api["paths"][0].endswith("/chat/completions")
    assert body["model"] == DEFAULT_MODEL
    assert body["max_completion_tokens"] == 16000
    assert {tool["function"]["name"] for tool in body["tools"]} >= {"summarize_period", "get_daily_report"}
    assert all(tool["type"] == "function" and tool["function"]["strict"] for tool in body["tools"])


def test_model_can_be_changed_from_environment(api, monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "my-model")
    api["responses"] = [ANSWER]
    list(run_turn(api["client"], [], "안녕"))
    assert api["requests"][0]["model"] == "my-model"


def test_system_prompt_carries_the_spec_promises(api):
    api["responses"] = [ANSWER]
    list(run_turn(api["client"], [], "안녕"))
    first = api["requests"][0]["messages"][0]
    assert first["role"] == "system"
    for promise in ("가능성", "원화 금액", "제어하지", "답할 수 없는 질문"):
        assert promise in first["content"]


def test_tool_error_is_reported_to_model_not_raised(api):
    api["responses"] = [BAD_TOOL_CALL, ANSWER]
    events = list(run_turn(api["client"], [], "2019년은?"))
    assert events[1] == {"type": "tool_result", "name": "summarize_period", "is_error": True}
    assert api["requests"][1]["messages"][-1]["content"].startswith("오류:")


def test_broken_tool_arguments_are_reported_to_model(api):
    broken = completion(
        {"content": None, "tool_calls": [{"id": "call_3", "type": "function", "function": {"name": "summarize_period", "arguments": "{"}}]},
        "tool_calls",
    )
    api["responses"] = [broken, ANSWER]
    events = list(run_turn(api["client"], [], "질문"))
    assert events[1]["is_error"] is True
    assert events[-2]["type"] == "answer"


def test_followup_question_keeps_history(api):
    api["responses"] = [TOOL_CALL, ANSWER, ANSWER]
    history = []
    list(run_turn(api["client"], history, "첫 질문"))
    list(run_turn(api["client"], history, "두 번째 질문"))
    messages = api["requests"][-1]["messages"]
    assert messages[1]["content"] == "첫 질문"
    assert messages[-1]["content"] == "두 번째 질문"


def test_refusal_leaves_history_untouched(api):
    api["responses"] = [completion({"content": None, "refusal": "거절"}, "stop")]
    history = []
    events = list(run_turn(api["client"], history, "질문"))
    assert events[-1]["type"] == "error"
    assert history == []


def test_endless_tool_calls_stop_at_limit(api):
    api["responses"] = [TOOL_CALL]
    history = []
    events = list(run_turn(api["client"], history, "질문"))
    assert events[-1]["type"] == "error"
    assert len(api["requests"]) == MAX_TOOL_ROUNDS
    assert history == []


def test_length_limited_answer_is_marked_as_cut(api):
    api["responses"] = [completion({"content": "일부"}, "length")]
    events = list(run_turn(api["client"], [], "질문"))
    assert "잘렸습니다" in events[-2]["text"]


def test_answer_numbers_are_checked_against_tool_results(api):
    from app import analysis

    s = analysis.summarize("2018-01-01", "2018-01-31")
    grounded = f"1월 사용량은 {s['usage_kwh']:,.0f}kWh이고 최대 수요전력은 {s['peak_kw']}kW({s['peak_at'][-5:]})였습니다."
    api["responses"] = [TOOL_CALL, completion({"content": grounded}, "stop")]
    verification = list(run_turn(api["client"], [], "1월 요약해줘"))[-1]
    assert verification["type"] == "verification"
    assert verification["ok"] and verification["checked"] >= 3 and verification["ungrounded"] == []


def test_made_up_numbers_are_flagged(api):
    api["responses"] = [TOOL_CALL, completion({"content": "1월에는 평소보다 37.5% 많은 98,765kWh를 썼습니다."}, "stop")]
    verification = list(run_turn(api["client"], [], "1월 요약해줘"))[-1]
    assert not verification["ok"]
    assert set(verification["ungrounded"]) == {"37.5", "98,765"}
