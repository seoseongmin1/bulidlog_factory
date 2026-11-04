"""경보 예측: '지금' 뒤의 측정값을 쓰지 않고, 9~12월 채점에서 비교 방식보다 헛경보가 적어야 한다."""
import numpy as np
import pytest

from app import forecast
from app.data import load_data


def test_backtest_ml_beats_comparisons():
    result = forecast.backtest()
    assert result["limit_kw"] == 408.1
    for target in forecast.TARGETS:
        rows = result["results"][target]
        assert rows["ml"]["초과_구간"] > 200
        assert rows["ml"]["하루_헛경보"] < min(rows["base"]["하루_헛경보"], rows["naive"]["하루_헛경보"])
        assert rows["ml"]["미리_잡은_비율_pct"] >= 90
        assert rows["ml"]["평균_몇분_전"] >= 30


@pytest.mark.parametrize("time", ["2018-10-10 13:00", "2018-10-10 23:30"])
def test_features_do_not_use_values_after_now(time):
    df = load_data()
    now = df.index.get_loc(time)
    changed = df.copy()
    changed.iloc[now + 1 :, changed.columns.get_loc("usage_kwh")] = 999.0
    changed.iloc[now + 1 :, changed.columns.get_loc("lag_kvarh")] = 0.0
    origin = np.array([now])
    before = forecast._features(forecast._prepare(df), origin)
    after = forecast._features(forecast._prepare(changed), origin)
    np.testing.assert_array_equal(before, after)


def test_forecast_shape_and_levels():
    result = forecast.forecast_at("2018-10-10 13:00")
    assert result["until"] == "2018-10-10 14:00"
    assert result["learned_period"] is False
    assert result["usage"]["limit_kw"] == 408.1
    for target in ("usage", "power_factor"):
        assert result[target]["level"] in forecast.LEVELS
        assert result[target]["label"] == forecast.LEVELS[result[target]["level"]]
        assert 0 <= result[target]["probability_pct"] <= 100
        assert result[target]["action"] == forecast.ACTIONS[target][result[target]["level"]]
    assert forecast.forecast_at("2018-03-14 10:00")["learned_period"] is True


def test_power_factor_is_not_watched_at_night():
    night = forecast.forecast_at("2018-12-16 03:00")["power_factor"]
    assert night["watched"] is False
    assert night["level"] == "good" and night["probability_pct"] == 0
    assert night["now_pct"] < forecast.PF_LIMIT
    assert "09~22시" in night["action"]


@pytest.mark.parametrize(
    ("time", "message"),
    [
        ("2019-01-01 00:00", "2018-01-01 00:00 ~ 2018-12-31 22:45"),
        ("2018-12-31 23:00", "2018-12-31 22:45"),
        ("2018-05-01 10:07", "15분 단위"),
        ("어제 아침", "YYYY-MM-DD HH:MM"),
    ],
)
def test_bad_time_is_explained(time, message):
    with pytest.raises(ValueError, match=message):
        forecast.forecast_at(time)


def test_forecast_api_returns_alarm_and_scores(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client = TestClient(app)
    response = client.get("/api/forecast", params={"time": "2018-10-10 13:00"})
    assert response.status_code == 200
    body = response.json()
    assert body["usage"]["level"] == "danger"
    assert body["scores"]["usage"] == forecast.backtest()["results"]["usage"]["ml"]
    bad = client.get("/api/forecast", params={"time": "2018-12-31 23:00"})
    assert bad.status_code == 400
    assert "2018-12-31 22:45" in bad.json()["detail"]


def test_reasons_only_for_alarms_and_explain_with_real_values():
    danger = forecast.forecast_at("2018-10-10 13:00")["usage"]
    assert danger["level"] == "danger" and 1 <= len(danger["reasons"]) <= forecast.REASON_TOP
    points = [r["points"] for r in danger["reasons"]]
    assert points == sorted(points, reverse=True) and min(points) >= forecast.REASON_MIN_POINTS
    assert all(r["detail"] and r["name"] for r in danger["reasons"])
    # 가장 큰 이유는 방금까지의 흐름(지금 값과 1시간 전 값)이고, 문장의 숫자는 데이터의 값이다.
    top = danger["reasons"][0]
    assert top["name"] == "방금까지의 흐름" and f"{danger['now_kw']:.1f}kW" in top["detail"]
    # 경보가 아니거나 역률을 보지 않는 시간에는 이유를 내지 않는다.
    night = forecast.forecast_at("2018-12-16 03:00")
    assert night["usage"]["reasons"] == [] and night["power_factor"]["reasons"] == []


def test_day_timeline_matches_single_forecasts():
    timeline = forecast.day_timeline("2018-10-10")
    rows = {row["time"]: row for row in timeline["rows"]}
    assert len(rows) == 96 and timeline["learned_period"] is False
    single = forecast.forecast_at("2018-10-10 13:00")
    assert rows["13:00"]["usage"] == single["usage"]["level"]
    assert rows["13:00"]["usage_pct"] == single["usage"]["probability_pct"]
    assert rows["13:00"]["kw"] == single["usage"]["now_kw"]
    # 밤에는 역률을 보지 않고, 실제로 기준을 넘은 칸은 kW가 기준보다 크다.
    assert rows["03:00"]["power_factor"] is None
    assert all(row["kw"] > timeline["limit_kw"] for row in rows.values() if row["over"])
    usage = timeline["summary"]["usage"]
    assert usage["초과_구간"] == 8 and usage["미리_잡은_비율_pct"] == 100.0
    # 재생 화면이 쓰는 할 일 문장은 단일 예측과 같은 것이다.
    assert timeline["actions"]["usage"][single["usage"]["level"]] == single["usage"]["action"]


def test_day_timeline_last_hour_and_bad_dates():
    last = forecast.day_timeline("2018-12-31")["rows"]
    assert [row["usage"] for row in last[-4:]] == [None] * 4
    assert last[-5]["usage"] in forecast.LEVELS
    for date in ("2019-01-01", "2018-12-16 10:00", "내일"):
        with pytest.raises(ValueError):
            forecast.day_timeline(date)


def test_day_timeline_api():
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    assert client.get("/api/forecast/day/2018-10-10").json()["summary"]["usage"]["초과_구간"] == 8
    bad = client.get("/api/forecast/day/2019-01-01")
    assert bad.status_code == 400 and "2018-01-01 ~ 2018-12-31" in bad.json()["detail"]


def test_agent_alarm_tools():
    import json

    from app.tools import openai_tools, run_tool

    names = {tool["function"]["name"] for tool in openai_tools()}
    assert {"forecast_alarm", "get_alarm_timeline", "summarize_alarms"} <= names and len(names) == 14

    text, chart, is_error = run_tool("forecast_alarm", {"time": "2018-10-10 13:00"})
    result = json.loads(text)
    assert not is_error and chart is None
    assert result["usage"]["label"] == "위험" and result["모델_채점_9_12월"] == forecast.scores()

    text, chart, is_error = run_tool("get_alarm_timeline", {"date": "2018-10-10"})
    result = json.loads(text)
    assert not is_error and len(chart["rows"]) == 96 and chart["unit"] == "kW"
    assert len(result["사용량_실제_초과_구간"]) == result["그날_채점"]["usage"]["초과_구간"] == 8
    assert all(span["kw_최고"] > 408.1 for span in result["사용량_실제_초과_구간"])

    text, chart, is_error = run_tool("summarize_alarms", {"start": "2018-09-01", "end": "2018-12-31"})
    result = json.loads(text)
    assert not is_error and len(chart["rows"]) == 24
    # 9~12월 전체 요약은 채점 결과와 같아야 한다.
    for key in ("초과_구간", "경보_횟수", "헛경보"):
        assert result["사용량"][key] == forecast.scores()["usage"][key]
        assert result["역률"][key] == forecast.scores()["power_factor"][key]

    for name, args in (("forecast_alarm", {"time": "2019-01-01 00:00"}), ("summarize_alarms", {"start": "2018-12-31", "end": "2018-12-01"})):
        _, _, is_error = run_tool(name, args)
        assert is_error
