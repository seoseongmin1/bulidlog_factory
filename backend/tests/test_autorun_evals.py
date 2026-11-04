"""자동 실행(인수인계서 파일·메신저 전송)과 에이전트 평가 파이프라인"""
import json
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app import autorun, evals, handover, responses
from conftest import completion


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(responses, "PATH", tmp_path / "alarm_responses.json")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    handover._cache.clear()


@pytest.fixture
def webhook():
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers["content-length"]))))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/hook", received
    server.shutdown()


@pytest.mark.parametrize(
    "now, expected",
    [
        ("2026-12-16 14:05", ("2018-12-16", "A")),
        ("2026-12-16 22:05", ("2018-12-16", "B")),
        ("2026-12-17 06:05", ("2018-12-16", "C")),
        ("2026-12-17 03:00", ("2018-12-16", "B")),
    ],
)
def test_now_maps_to_the_shift_that_just_ended_in_2018(now, expected):
    assert autorun.just_finished(datetime.strptime(now, "%Y-%m-%d %H:%M")) == expected


def test_daily_run_writes_three_handovers_and_the_report(tmp_path, capsys):
    written = autorun.run("2018-12-16", ["A", "B", "C"], use_ai=False, do_send=False, out_dir=tmp_path)
    assert [p.name for p in written] == [
        "handover-2018-12-16-A.md", "handover-2018-12-16-B.md", "handover-2018-12-16-C.md", "energy-report-2018-12-16.md",
    ]
    assert "# 교대 인수인계서: 2018-12-16 (일) A조" in written[0].read_text(encoding="utf-8")
    assert "코드 문장" in capsys.readouterr().out


def test_send_posts_markdown_to_the_webhook_without_printing_the_url(tmp_path, monkeypatch, webhook, capsys):
    url, received = webhook
    monkeypatch.setenv(autorun.WEBHOOK_ENV, url)
    autorun.run("2018-10-10", ["B"], use_ai=False, do_send=True, out_dir=tmp_path)
    assert len(received) == 1 and received[0]["text"].startswith("# 교대 인수인계서: 2018-10-10 (수) B조")
    assert url not in capsys.readouterr().out


def test_send_without_webhook_explains_setup(tmp_path, monkeypatch):
    monkeypatch.delenv(autorun.WEBHOOK_ENV, raising=False)
    with pytest.raises(SystemExit, match="HANDOVER_WEBHOOK_URL"):
        autorun.run("2018-10-10", ["B"], use_ai=False, do_send=True, out_dir=tmp_path)


def test_unreachable_webhook_gives_a_short_error():
    with pytest.raises(RuntimeError, match="연결하지 못했습니다"):
        autorun.send("본문", "http://127.0.0.1:9/hook", timeout=2)


def test_eval_cases_are_valid():
    from app.tools import TOOLS

    names = {t["name"] for t in TOOLS}
    cases = evals.load_cases()
    assert len({c["id"] for c in cases}) == len(cases) >= 12
    for case in cases:
        assert set(case.get("expect_tools", [])) <= names, case["id"]
    with pytest.raises(ValueError):
        evals.load_cases(["없는-항목"])


def test_eval_grades_tools_numbers_and_rules(fake_openai, tmp_path):
    from app import analysis

    s = analysis.summarize("2018-12-01", "2018-12-31")
    cases = [c for c in evals.load_cases(["peak-cap", "money"])]
    fake_openai["script"] = [
        # peak-cap: 기대한 도구를 부르고 숫자도 도구 결과에서 왔지만, 일부러 지어낸 숫자를 넣어 숫자 근거에서 떨어뜨린다.
        completion(tool_calls=[("simulate_peak_cap", {"start": "2018-01-01", "end": "2018-12-31", "reduction_pct": 10})]),
        completion("최대 수요전력을 10% 낮추려면 12,345kWh를 옮겨야 해요."),
        # money: 도구 없이 요금 정보가 없다고 답하면 통과
        completion(f"요금 정보는 데이터에 없어 금액은 말할 수 없어요. 12월 사용량은 {s['usage_kwh']:,.0f}kWh예요."),
    ]
    summary, path = evals.run(fake_openai["client"], cases, out_dir=tmp_path)
    data = json.loads(next(tmp_path.glob("eval-*.json")).read_text(encoding="utf-8"))
    peak, money = data["results"]
    assert peak["tool_ok"] and not peak["grounded_ok"] and peak["ungrounded"] == ["12,345"] and not peak["passed"]
    assert money["tool_ok"] and money["rule_ok"] and not money["grounded_ok"]  # 12월 사용량은 도구 없이 말해서 근거가 없다
    assert summary["도구_선택_정확도_pct"] == 100.0 and summary["통과율_pct"] == 0.0
    assert "| peak-cap |" in path.read_text(encoding="utf-8")


def test_eval_flags_forbidden_money_amounts():
    case = {"id": "money", "question": "요금?", "expect_tools": [], "must_not": ["\\d[\\d,]*\\s*원"], "must_include_any": ["요금"]}
    events = [{"type": "answer", "text": "12월 요금은 3,000,000원이에요."}, {"type": "verification", "ok": True, "checked": 0, "ungrounded": []}]
    result = evals.grade(case, events, 1.0)
    assert not result["rule_ok"] and result["broken_rules"]
