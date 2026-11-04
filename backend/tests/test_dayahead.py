import json

import pytest
from fastapi.testclient import TestClient

from app import dayahead, forecast
from app.main import app


def test_backtest_matches_scoring_table():
    result = dayahead.backtest()
    assert result["limit_kw"] == 408.1
    warn, danger = result["results"]["freq_warn"], result["results"]["freq_danger"]
    assert warn["초과_구간"] == danger["초과_구간"] == 144
    # 위험은 주의보다 더 높은 문턱이라 켜지는 구간이 적고 놓치는 것은 많다.
    assert danger["구간_켜진_비율_pct"] < warn["구간_켜진_비율_pct"]
    assert danger["미리_잡은_비율_pct"] < warn["미리_잡은_비율_pct"]
    # 켜진 구간 중 실제로 넘은 비율은 아무 구간이나 고를 때(기준선)보다 높아야 의미가 있다.
    assert warn["켜진_구간_중_실제_pct"] > result["아무_구간이나_초과_pct"] * 2
    assert warn["미리_잡은_비율_pct"] > result["results"]["naive"]["미리_잡은_비율_pct"]


def test_day_ahead_shape_and_plan():
    result = dayahead.day_ahead("2018-10-10")
    assert result["issued_at"] == "2018-10-09 23:45" and result["day_type"] == "평일" and result["learned_period"] is False
    assert len(result["blocks"]) == 12 and len(result["rows"]) == 96
    assert result["blocks"][0]["start"] == "00:00" and result["blocks"][-1]["end"] == "24:00"
    risky = [b for b in result["blocks"] if b["level"] != "good"]
    assert risky and len(result["plan"]) == len(risky)
    assert all(b["compared_days"] > 0 and b["exceeded_days"] <= b["compared_days"] for b in result["blocks"])
    summary = result["result"]
    assert summary["위험_구간"] == len(risky)
    assert summary["맞힌_구간"] + summary["놓친_구간"] == summary["실제_초과_구간"]
    assert summary["맞힌_구간"] + summary["헛_구간"] == summary["위험_구간"]
    # 실제로 기준을 넘은 구간은 그 구간 안의 실제 최고 kW가 기준보다 크다.
    assert all((b["actual_max_kw"] > result["limit_kw"]) == b["over"] for b in result["blocks"])


def test_quiet_day_has_no_plan():
    result = dayahead.day_ahead("2018-12-16")  # 일요일
    assert result["day_type"] == "일요일·공휴일" and result["result"]["위험_구간"] == 0
    assert result["plan"] == [dayahead.NONE_NOTE]


def test_future_values_are_not_used(monkeypatch):
    """예측할 날의 측정값을 크게 바꿔도 그날의 비교 빈도(위험 시간대)는 그대로고, 다음 날부터만 달라진다."""
    base = dayahead._model()
    day = 282  # 2018-10-10
    changed_series = dict(base["s"])
    changed_series["kw"] = base["s"]["kw"].copy()
    changed_series["kw"][day * 96 : (day + 1) * 96] += 1000
    monkeypatch.setattr(forecast, "load_series", lambda: changed_series)
    changed = dayahead._model_for.__wrapped__("테스트")
    assert changed["over"][day].all()  # 바꾼 값은 실제로 반영됐다.
    assert (changed["freq"][day] == base["freq"][day]).all()
    assert not (changed["freq"][day + 1] == base["freq"][day + 1]).all() or not (changed["freq"][day + 7] == base["freq"][day + 7]).all()


@pytest.mark.parametrize("date", ["2018-01-07", "2019-01-01", "2018-10-10 10:00", "내일"])
def test_bad_dates(date):
    with pytest.raises(ValueError):
        dayahead.day_ahead(date)


def test_first_predictable_day():
    assert dayahead.day_ahead("2018-01-08")["learned_period"] is True


def test_day_ahead_api():
    client = TestClient(app)
    body = client.get("/api/day-ahead/2018-10-10").json()
    assert body["blocks"] and body["scores"]["results"]["freq_warn"]["초과_구간"] == 144
    bad = client.get("/api/day-ahead/2018-01-03")
    assert bad.status_code == 400 and "2018-01-08 ~ 2018-12-31" in bad.json()["detail"]


def test_agent_tool():
    from app.tools import run_tool

    text, chart, is_error = run_tool("forecast_day_ahead", {"date": "2018-10-10"})
    result = json.loads(text)
    assert not is_error and result["예측_시점"] == "2018-10-09 23:45"
    assert result["위험_시간대"] and result["모델_채점_9_12월"]["results"]["freq_warn"]
    assert chart["kind"] == "line" and chart["rows"] == result["rows"] and len(chart["rows"]) == 96
    text, chart, is_error = run_tool("forecast_day_ahead", {"date": "2019-01-01"})
    assert is_error and chart is None and "예측할 수 있는 날짜" in text
