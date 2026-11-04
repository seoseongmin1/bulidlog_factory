"""교대 인수인계서: 숫자와 상태는 코드가 정하고, AI 문장은 사실의 숫자만 쓸 때만 쓴다."""
import json

import pytest
from fastapi.testclient import TestClient

from app import handover, responses
from app.main import app
from conftest import completion

client = TestClient(app)


@pytest.fixture(autouse=True)
def temp_responses(tmp_path, monkeypatch):
    monkeypatch.setattr(responses, "PATH", tmp_path / "alarm_responses.json")
    handover._cache.clear()


def test_shift_windows_cover_the_day_and_the_last_night_is_partial():
    start, end, partial = handover.window("2018-12-16", "C")
    assert (str(start), str(end), partial) == ("2018-12-16 22:00:00", "2018-12-17 06:00:00", False)
    start, end, partial = handover.window("2018-12-31", "C")
    assert str(end) == "2019-01-01 00:00:00" and partial
    for bad in (("2019-01-01", "A"), ("2018-12-16", "D"), ("어제", "A")):
        with pytest.raises(ValueError):
            handover.window(*bad)


def test_three_shifts_add_up_to_the_daily_usage_of_their_hours():
    from app.data import load_data

    df = load_data()
    total = sum(handover.facts("2018-10-10", s)["사용량_kwh"] for s in "AB")
    expected = df.loc["2018-10-10 06:00":"2018-10-10 21:45", "usage_kwh"].sum()
    assert total == pytest.approx(expected, abs=0.2)


def test_busy_shift_lists_alarms_exceedances_and_next_hour():
    h = handover.handover("2018-10-10", "B", None)
    f = h["facts"]
    usage = f["경보"]["usage"]
    assert len(usage["초과"]) == 4 and all(e["최고_kw"] > f["사용량_기준_kw"] for e in usage["초과"])
    assert all(e["몇분_먼저"] == 60 for e in usage["초과"])
    assert h["status"]["level"] == "bad"  # 응답 없는 위험 경보
    assert any("응답이 없어요" in item for item in h["note"]["items"])
    assert h["note"]["next"][0].startswith("다음 조 시작 1시간(22:45까지)")
    assert "[SOP-EN-01 2. 경보 단계별 조치]" in " ".join(h["note"]["next"])
    assert h["source"] == "rule" and "## 인계 사항" in h["markdown"]


def test_operator_responses_close_open_alarms():
    first = handover.facts("2018-10-10", "B")["경보"]["usage"]["경보"]
    for alarm in first:
        responses.record(alarm["time"], "usage", alarm["level"], alarm["처음_확률_pct"], "acted")
    f = handover.facts("2018-10-10", "B")
    assert all(a["응답"] == "조치함" for a in f["경보"]["usage"]["경보"])
    assert f["응답"]["조치함"] == len(first)
    note = handover.handover("2018-10-10", "B", None)["note"]
    assert not any(item.startswith("사용량 경보") and "응답이 없어요" in item for item in note["items"])


def test_quiet_night_is_good_and_power_factor_is_not_watched():
    h = handover.handover("2018-12-16", "C", None)
    assert h["status"]["level"] == "good"
    assert h["facts"]["역률_pct"] is None and "09~22시만" in h["note"]["summary"][1]
    assert h["note"]["items"] == ["특별히 넘길 일은 없어요."]


def test_sunday_overuse_points_to_the_overtime_procedure():
    h = handover.handover("2018-12-16", "A", None)
    assert "10.0배" in h["note"]["summary"][0]
    assert any(r["doc_id"] == "SOP-OP-03" for r in h["manual_refs"])


def test_last_shift_of_the_data_has_no_next_hour():
    h = handover.handover("2018-12-31", "C", None)
    assert h["facts"]["다음_조_1시간"] is None and "예측할 수 없어요" in h["note"]["next"][0]
    assert "일부만" in h["markdown"]


def _ai_reply(note):
    return completion(json.dumps({"summary": note["summary"], "items": note["items"], "next": note["next"]}, ensure_ascii=False))


def test_ai_note_is_used_when_numbers_and_citations_are_kept(fake_openai):
    rule = handover.handover("2018-10-10", "B", None)["note"]
    polished = {**rule, "summary": [line.replace("썼어요.", "사용했어요.") for line in rule["summary"]]}
    fake_openai["script"] = [_ai_reply(polished)]
    h = handover.handover("2018-10-10", "B", fake_openai["client"])
    assert h["source"] == "ai" and h["note"]["summary"] == polished["summary"]
    assert fake_openai["requests"][0]["response_format"]["type"] == "json_schema"


def test_ai_note_with_a_new_number_is_rejected(fake_openai):
    rule = handover.handover("2018-10-10", "B", None)["note"]
    bad = {**rule, "summary": ["B조는 평소보다 3,000kWh 더 썼어요.", *rule["summary"][1:]]}
    fake_openai["script"] = [_ai_reply(bad)]
    h = handover.handover("2018-10-10", "B", fake_openai["client"])
    assert h["source"] == "rule" and h["note"] == rule


def test_ai_note_that_drops_a_manual_citation_is_rejected(fake_openai):
    rule = handover.handover("2018-10-10", "B", None)["note"]
    dropped = {**rule, "next": [line.replace("[SOP-EN-01 2. 경보 단계별 조치]", "매뉴얼") for line in rule["next"]]}
    fake_openai["script"] = [_ai_reply(dropped)]
    assert handover.handover("2018-10-10", "B", fake_openai["client"])["source"] == "rule"


def test_api_returns_rule_note_without_key_and_rejects_bad_input(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    good = client.get("/api/handover/2018-10-10/B")
    assert good.status_code == 200 and good.json()["source"] == "rule"
    assert client.get("/api/handover/2018-10-10/X").status_code == 400
    assert client.get("/api/handover/2019-01-01/A").status_code == 400
