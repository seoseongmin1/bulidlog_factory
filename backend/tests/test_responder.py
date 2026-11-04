"""조치 에이전트(LLM 루프): 가짜 OpenAI 서버로 실제 SDK를 통과시킨다."""
import json

import pytest
from fastapi.testclient import TestClient

from app import actions, responder
from app.main import app
from conftest import completion


@pytest.fixture(autouse=True)
def temp_files(tmp_path, monkeypatch):
    monkeypatch.setattr(actions, "LOG_PATH", tmp_path / "log.jsonl")
    monkeypatch.setattr(actions, "WORK_ORDER_PATH", tmp_path / "work_orders.json")
    monkeypatch.delenv("HANDOVER_WEBHOOK_URL", raising=False)


SHED = {"start": "13:15", "minutes": 60, "kw": 40}


def test_agent_investigates_simulates_and_proposes(fake_openai):
    fake_openai["script"] = [
        completion("먼저 사건을 볼게요.", tool_calls=[("get_incident", {})]),
        completion(tool_calls=[("simulate_action", {"action_type": "shed_auxiliary", "params": SHED})]),
        completion(tool_calls=[("propose_action", {"action_type": "defer_load", "params": {"start": "13:15", "minutes": 60, "kw": 500}, "reason": "크게"})]),
        completion(tool_calls=[("propose_action", {"action_type": "shed_auxiliary", "params": SHED, "reason": "보조 설비 정지"}),
                               ("propose_action", {"action_type": "notify", "params": {"to": "반장", "message": "보조 설비를 멈춰 주세요."}, "reason": "알림"})]),
        completion(tool_calls=[("finish", {"summary": "보조 설비 정지를 제안하고 반장에게 알렸어요."})]),
    ]
    incident = responder.respond("2018-10-10 13:00", fake_openai["client"])
    assert incident["planner"] == "ai" and incident["status"] == "waiting_approval"
    assert [(a["type"], a["status"]) for a in incident["actions"]] == [("shed_auxiliary", "pending"), ("notify", "executed")]
    kinds = [s["kind"] for s in incident["steps"]]
    assert kinds[0] == "detect" and "simulate" in kinds and "blocked" in kinds and kinds[-1] == "finish"
    assert incident["summary"].startswith("보조 설비 정지")
    # 한도를 넘은 제안은 오류로 모델에게 돌아간다.
    tool_messages = [m for m in fake_openai["requests"][3]["messages"] if m["role"] == "tool"]
    assert tool_messages[-1]["content"].startswith("오류: kw는 10~150")
    # 에이전트가 받은 사건 요약에는 실제 미래 값이 없다.
    first_tool = next(m for m in fake_openai["requests"][1]["messages"] if m["role"] == "tool")
    assert "452.6" not in first_tool["content"]
    assert {t["function"]["name"] for t in fake_openai["requests"][0]["tools"]} == {"get_incident", "simulate_action", "propose_action", "search_manual", "finish"}


def test_unresolved_result_goes_back_to_the_agent(fake_openai):
    fake_openai["script"] = [
        completion(tool_calls=[("propose_action", {"action_type": "shed_auxiliary", "params": SHED, "reason": "줄이기"})]),
        completion(tool_calls=[("finish", {"summary": "승인을 기다려요."})]),
        completion(tool_calls=[("propose_action", {"action_type": "notify", "params": {"to": "에너지 담당", "message": "생산 일정 조정이 필요해요."}, "reason": "넘김"})]),
        completion(tool_calls=[("finish", {"summary": "한도를 넘어 에너지 담당에게 넘겼어요."})]),
    ]
    incident = responder.respond("2018-10-10 13:00", fake_openai["client"])
    actions.decide(incident, "A1", True)
    responder.after_decision(incident, fake_openai["client"])
    assert incident["status"] == "unresolved"  # 40kW로는 452.6kW를 기준 아래로 못 내린다.
    replan = fake_openai["requests"][2]["messages"][-1]
    assert replan["role"] == "user" and "아직 해결되지 않았습니다" in replan["content"] and "조치_후_실제" in replan["content"]
    assert incident["actions"][-1]["params"]["to"] == "에너지 담당" and incident["summary"].startswith("한도를 넘어")


def test_ai_failure_falls_back_to_the_rule_planner(fake_openai):
    fake_openai["script"] = [{"error": "bad"}]  # SDK가 읽을 수 없는 응답
    incident = responder.respond("2018-09-07 20:00", fake_openai["client"])
    assert incident["planner"].startswith("rule") and [a["type"] for a in incident["actions"]] == ["defer_load", "notify"]


def test_api_flow_without_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client = TestClient(app)
    data = client.post("/api/incidents", json={"time": "2018-09-07 20:00"}).json()
    assert data["planner"] == "rule" and data["status"] == "waiting_approval" and "_base" not in data
    assert len(data["expected"]["rows"]) == actions.HORIZON
    approved = client.post(f"/api/incidents/{data['id']}/actions/A1/approve").json()
    assert approved["status"] == "resolved" and approved["verification"]["with"]["over_slots"] == 0
    assert client.post(f"/api/incidents/{data['id']}/actions/A1/approve").status_code == 409
    assert client.post(f"/api/incidents/{data['id']}/actions/A9/approve").status_code == 404
    assert client.post(f"/api/incidents/{data['id']}/actions/A1/explode").status_code == 404
    reverted = client.post(f"/api/incidents/{data['id']}/actions/A1/revert").json()
    assert reverted["status"] == "unresolved"
    assert client.get("/api/incidents/nope").status_code == 404
    assert client.post("/api/incidents", json={"time": "2018-12-31 23:00"}).status_code == 400
    assert client.get("/api/work-orders").json() == {"items": []}
