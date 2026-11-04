"""조치 실행 계층: 안전장치(허용 조치·한도·승인), 가상 공장 효과, 실제 기록 검증, 규칙 계획자"""
import json

import pytest

from app import actions


@pytest.fixture(autouse=True)
def temp_files(tmp_path, monkeypatch):
    monkeypatch.setattr(actions, "LOG_PATH", tmp_path / "log.jsonl")
    monkeypatch.setattr(actions, "WORK_ORDER_PATH", tmp_path / "work_orders.json")
    monkeypatch.delenv("HANDOVER_WEBHOOK_URL", raising=False)


def approve_all(incident):
    for a in incident["actions"]:
        if a["status"] == "pending":
            actions.decide(incident, a["id"], True)


def test_incident_knows_only_the_past():
    incident = actions.open_incident("2018-10-10 13:00")
    assert incident["targets"] == ["usage"] and incident["status"] == "open"
    assert incident["_base"]["times"][0] == "13:15" and len(incident["_base"]["times"]) == actions.HORIZON
    # 에이전트에게 주는 요약에는 실제 미래 값(14:00의 452.6kW)이 없다.
    overview = json.dumps(actions.overview(incident), ensure_ascii=False)
    assert "452.6" not in overview and "예상_불확실성_kw" in overview


def test_quiet_moment_needs_no_action():
    incident = actions.open_incident("2018-06-20 03:00")
    assert incident["targets"] == [] and incident["status"] == "no_action"


@pytest.mark.parametrize(
    "action_type, params, message",
    [
        ("open_valve", {}, "허용되지 않은 조치"),
        ("defer_load", {"start": "13:15", "minutes": 60, "kw": 500}, "kw는 10~150"),
        ("shed_auxiliary", {"start": "13:15", "minutes": 60, "kw": 80}, "kw는 10~40"),
        ("defer_load", {"start": "12:00", "minutes": 30, "kw": 50}, "지난 시각"),
        ("defer_load", {"start": "13:15", "minutes": 20, "kw": 50}, "15분 단위"),
        ("defer_load", {"start": "14:15", "minutes": 60, "kw": 50}, "2시간을 넘"),
        ("capacitor_step", {"start": "13:15", "minutes": 60, "steps": 9}, "steps는 1~4"),
        ("notify", {"to": "사장님", "message": "안녕"}, "to는"),
        ("create_work_order", {"assignee": "반장", "title": "", "due_minutes": 60}, "title"),
    ],
)
def test_limits_are_enforced_by_code(action_type, params, message):
    incident = actions.open_incident("2018-10-10 13:00")
    with pytest.raises(ValueError, match=message):
        actions.propose(incident, action_type, params, "시험")
    assert incident["actions"] == []


def test_low_risk_runs_now_and_high_risk_waits_for_a_person(tmp_path):
    incident = actions.open_incident("2018-10-10 13:00")
    note = actions.propose(incident, "notify", {"to": "반장", "message": "기동을 미뤄 주세요."}, "알림")
    order = actions.propose(incident, "create_work_order", {"assignee": "전기보전팀", "title": "점검", "due_minutes": 60}, "점검")
    plant = actions.propose(incident, "shed_auxiliary", {"start": "13:15", "minutes": 60, "kw": 40}, "줄이기")
    assert note["status"] == order["status"] == "executed" and note["result"]["전달"].startswith("이 컴퓨터")
    assert plant["status"] == "pending" and incident["status"] == "waiting_approval"
    assert actions.work_orders()[0]["title"] == "점검" and order["result"]["작업지시서"] == "WO-0001"

    actions.decide(incident, plant["id"], True)
    assert incident["actions"][2]["status"] == "executed" and incident["actions"][2]["executed_by"] == "user"
    assert incident["verification"]["with"]["peak_kw"] == pytest.approx(incident["verification"]["without"]["peak_kw"] - 40, abs=0.2)
    with pytest.raises(ValueError):
        actions.decide(incident, plant["id"], True)

    actions.revert(incident, plant["id"])
    assert incident["verification"]["with"]["peak_kw"] == incident["verification"]["without"]["peak_kw"]
    log = [json.loads(line) for line in (tmp_path / "log.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [line["kind"] for line in log if line["kind"] in ("approve", "revert")] == ["approve", "revert"]
    assert {line["who"] for line in log} >= {"system", "agent", "user"}


def test_rejected_action_never_runs():
    incident = actions.open_incident("2018-10-10 13:00")
    plant = actions.propose(incident, "defer_load", {"start": "13:15", "minutes": 60, "kw": 70}, "미루기")
    actions.decide(incident, plant["id"], False)
    assert incident["actions"][0]["status"] == "rejected" and incident["verification"] is None


def test_deferring_moves_energy_without_losing_it():
    incident = actions.open_incident("2018-10-10 13:00")
    before = actions._state(incident, "actual", ())["kwh"].sum()
    plant = actions.propose(incident, "defer_load", {"start": "13:15", "minutes": 60, "kw": 70}, "미루기")
    actions.decide(incident, plant["id"], True)
    assert actions._state(incident, "actual")["kwh"].sum() == pytest.approx(before)
    assert incident["verification"]["with"]["peak_kw"] < incident["verification"]["without"]["peak_kw"]


def test_too_many_capacitors_overcompensate():
    incident = actions.open_incident("2018-09-05 10:00")
    result = actions.simulate(incident, "capacitor_step", {"start": "10:15", "minutes": 120, "steps": 4})
    assert result["후"]["lead_slots"] > 0 and "보수적_최대_kw" in result


def test_rule_planner_resolves_a_shiftable_peak():
    incident = actions.open_incident("2018-09-07 20:00")
    actions.rule_plan(incident)
    assert [a["type"] for a in incident["actions"]] == ["defer_load", "notify"]
    approve_all(incident)
    assert incident["status"] == "resolved"
    assert incident["verification"]["without"]["over_slots"] > 0 and incident["verification"]["with"]["over_slots"] == 0


def test_rule_planner_escalates_when_the_load_stays_high():
    incident = actions.open_incident("2018-10-10 13:00")
    actions.rule_plan(incident)
    types = [(a["type"], a["params"].get("to")) for a in incident["actions"]]
    assert ("shed_auxiliary", None) in types and ("notify", "에너지 담당") in types
    assert not any(a["type"] == "defer_load" for a in incident["actions"])


def test_rule_planner_fixes_power_factor_without_overcompensating():
    incident = actions.open_incident("2018-09-03 14:30")
    actions.rule_plan(incident)
    cap = next(a for a in incident["actions"] if a["type"] == "capacitor_step")
    order = next(a for a in incident["actions"] if a["type"] == "create_work_order")
    assert order["status"] == "executed" and order["params"]["cite"] == "[SOP-EN-02 2. 경보가 켜지면]"
    approve_all(incident)
    v = incident["verification"]
    assert incident["status"] == "resolved" and v["with"]["lead_slots"] == 0 and v["with"]["min_pf"] > v["without"]["min_pf"]
    assert cap["expected"]["min_pf"][1] > cap["expected"]["min_pf"][0]


def test_backtest_shows_naive_deferring_backfires_and_the_rule_planner_helps():
    result = actions.backtest(step=16)["결과"]
    none, defer, rule = result["none"], result["defer"], result["rule"]
    assert defer["초과_kwh_후"] > none["초과_kwh_후"]  # 무조건 미루기는 오히려 초과를 늘린다.
    assert rule["초과_kwh_후"] < none["초과_kwh_후"] * 0.8
    assert rule["악화"] <= defer["악화"]
