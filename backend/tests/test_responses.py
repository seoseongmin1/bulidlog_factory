import pytest
from fastapi.testclient import TestClient

from app import forecast, responses
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def temp_file(tmp_path, monkeypatch):
    """실제 기록 파일(backend/local)을 건드리지 않도록 임시 파일을 쓴다."""
    monkeypatch.setattr(responses, "PATH", tmp_path / "alarm_responses.json")


def post(**overrides):
    body = {"time": "2018-10-10 12:15", "target": "usage", "level": "warn", "probability_pct": 12, "response": "acted", **overrides}
    return client.post("/api/alarm-responses", json=body)


def test_record_saves_to_file_and_same_slot_is_replaced():
    assert post().status_code == 200
    assert post(response="false_alarm").status_code == 200
    assert post(target="power_factor", level="danger", probability_pct=70, response="ignored").status_code == 200
    saved = responses._load()
    assert len(saved) == 2
    assert [r["response"] for r in saved if r["target"] == "usage"] == ["false_alarm"]
    assert client.get("/api/alarm-responses/day/2018-10-10").json() == {"12:15|usage": "false_alarm", "12:15|power_factor": "ignored"}
    assert client.get("/api/alarm-responses/day/2018-10-11").json() == {}


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"time": "내일"}, "시각 형식"),
        ({"time": "2018-10-10 12:07"}, "15분 단위"),
        ({"target": "kw"}, "대상은"),
        ({"level": "good"}, "주의·위험"),
        ({"response": "done"}, "응답은"),
        ({"probability_pct": 101}, "0~100"),
    ],
)
def test_bad_input_is_rejected_with_reason(overrides, message):
    result = post(**overrides)
    assert result.status_code == 400 and message in result.json()["detail"]
    assert responses._load() == []


def test_stats_count_and_cutoff_simulation():
    post(time="2018-10-10 09:00", probability_pct=12, response="false_alarm")
    post(time="2018-10-10 10:00", probability_pct=35, response="false_alarm")
    post(time="2018-10-10 13:00", level="danger", probability_pct=60, response="acted")
    post(time="2018-10-10 13:15", probability_pct=15, response="acted")
    post(time="2018-10-10 14:00", probability_pct=20, response="ignored")
    stats = client.get("/api/alarm-responses/stats").json()
    usage = stats["대상별"]["usage"]
    assert stats["총"] == 5 and usage["건수"] == 5
    assert (usage["조치함"], usage["무시함"], usage["오경보"]) == (2, 1, 2)
    assert usage["오경보_비율_pct"] == 50.0  # 무시함은 맞았는지 알 수 없어 뺀다.
    sim = {row["문턱_pct"]: row for row in usage["문턱_시뮬레이션"]}
    assert (sim[20]["사라지는_오경보"], sim[20]["놓치는_조치"]) == (1, 1)  # 12% 오경보는 사라지고 15% 조치는 놓친다.
    assert (sim[50]["사라지는_오경보"], sim[50]["놓치는_조치"]) == (2, 1)
    assert usage["현재_주의_문턱_pct"] == forecast.warn_cutoffs_pct()["usage"]
    assert stats["대상별"]["power_factor"]["오경보_비율_pct"] is None
    assert stats["시간대별_오경보"] == [{"label": "09시", "value": 1}, {"label": "10시", "value": 1}, {"label": "13시", "value": 0}, {"label": "14시", "value": 0}]


def test_empty_stats():
    stats = client.get("/api/alarm-responses/stats").json()
    assert stats["총"] == 0 and stats["시간대별_오경보"] == []
