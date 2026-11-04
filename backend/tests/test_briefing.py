"""일일 자동 요약: 상태는 코드가 정하고, AI 문장은 사실에 있는 숫자만 쓸 때만 받아들인다."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import openai
import pytest
from fastapi.testclient import TestClient

from app import briefing
from app.main import app

client = TestClient(app)


def test_status_comes_from_code():
    assert briefing.daily_briefing("2018-12-16", None)["status"] == {"level": "bad", "label": "이상"}
    # 휴일에 적게 쓴 날은 이상 점수가 높아도 '주의'다.
    assert briefing.daily_briefing("2018-01-01", None)["status"]["label"] == "주의"
    assert briefing.daily_briefing("2018-06-20", None)["status"]["label"] == "좋음"


def test_rule_summary_is_short_and_uses_report_numbers():
    result = briefing.daily_briefing("2018-12-16", None)
    assert result["source"] == "rule"
    assert len(result["lines"]) == 3
    assert "2,015kWh" in result["lines"][0] and "5.5배" in result["lines"][0]
    assert "10:15" in result["lines"][1]
    assert "작업일지" in result["action"]


def test_holiday_is_named_and_needs_no_action():
    result = briefing.daily_briefing("2018-09-25", None)
    assert "추석 연휴" in result["lines"][0]
    assert "할 일은 없어요" in result["action"]


def completion(content):
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 0,
        "model": "test",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


@pytest.fixture
def fake_ai():
    state = {"reply": None, "status": 200, "requests": []}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            state["requests"].append(json.loads(self.rfile.read(int(self.headers["content-length"]))))
            payload = json.dumps(state["reply"]).encode()
            self.send_response(state["status"])
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    state["client"] = openai.OpenAI(api_key="test", base_url=f"http://127.0.0.1:{server.server_port}/v1", max_retries=0)
    briefing._cache.clear()
    yield state
    briefing._cache.clear()
    server.shutdown()


def ai_reply(lines, action):
    return completion(json.dumps({"lines": lines, "action": action}, ensure_ascii=False))


def test_ai_sentences_are_used_when_numbers_match(fake_ai):
    fake_ai["reply"] = ai_reply(
        ["평소 주말보다 훨씬 많이, 2,015kWh를 썼어요.", "10:15에 가장 많이 썼어요.", "전기 효율은 괜찮아요."],
        "작업일지를 확인해 주세요.",
    )
    result = briefing.daily_briefing("2018-12-16", fake_ai["client"])
    assert result["source"] == "ai"
    assert result["status"]["label"] == "이상"
    assert result["lines"][0].startswith("평소 주말보다")
    body = fake_ai["requests"][0]
    assert body["response_format"]["type"] == "json_schema"
    assert "2,015kWh" in body["messages"][1]["content"]

    # 같은 날짜는 다시 묻지 않고, AI를 부르지 않는 빠른 요청에도 만든 문장을 준다.
    briefing.daily_briefing("2018-12-16", fake_ai["client"])
    assert len(fake_ai["requests"]) == 1
    assert briefing.daily_briefing("2018-12-16", None)["source"] == "ai"


def test_ai_sentence_with_made_up_number_is_rejected(fake_ai):
    fake_ai["reply"] = ai_reply(["3,000kWh를 썼어요.", "10:15에 많이 썼어요.", "괜찮아요."], "확인해 주세요.")
    result = briefing.daily_briefing("2018-12-16", fake_ai["client"])
    assert result["source"] == "rule"
    assert "2,015kWh" in result["lines"][0]


@pytest.mark.parametrize("reply", [completion("JSON 아님"), ai_reply(["한 줄만"], "확인")])
def test_broken_ai_answer_falls_back_to_rule(fake_ai, reply):
    fake_ai["reply"] = reply
    assert briefing.daily_briefing("2018-12-16", fake_ai["client"])["source"] == "rule"


def test_ai_error_falls_back_to_rule(fake_ai):
    fake_ai["status"], fake_ai["reply"] = 401, {"error": {"message": "bad key", "type": "invalid_request_error"}}
    result = briefing.daily_briefing("2018-12-16", fake_ai["client"])
    assert result["source"] == "rule"
    assert len(result["lines"]) == 3


def test_quick_request_does_not_call_ai(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setattr(briefing, "_ai_lines", lambda *_: pytest.fail("AI를 부르면 안 됩니다"))
    briefing._cache.clear()
    response = client.get("/api/briefing/2018-12-16?ai=false")
    assert response.status_code == 200 and response.json()["source"] == "rule"


def test_briefing_api_without_key_and_with_bad_date(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    briefing._cache.clear()
    good = client.get("/api/briefing/2018-12-16")
    assert good.status_code == 200
    assert good.json()["source"] == "rule" and good.json()["status"]["level"] == "bad"
    bad = client.get("/api/briefing/2019-01-01")
    assert bad.status_code == 400
    assert "2018-01-01 ~ 2018-12-31" in bad.json()["detail"]
