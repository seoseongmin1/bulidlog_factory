"""자동 실행: 교대 인수인계서와 일일 보고서를 파일로 만들고, 원하면 메신저(웹훅)로 보낸다.

사람이 화면을 열지 않아도 교대가 끝날 때마다 문서가 만들어지게 하는 명령이다. Windows 작업 스케줄러나 cron에 등록해 쓴다.

    python -m app.autorun --date 2018-12-16              # 그날 A·B·C조 인수인계서 + 일일 보고서를 파일로 저장
    python -m app.autorun --date 2018-10-10 --shift B --send
    python -m app.autorun --now --send                   # 지금 시각을 2018년의 같은 날·시각으로 보고, 방금 끝난 교대를 보낸다

데이터가 2018년 기록이라 --now는 오늘 날짜를 2018년의 같은 날로 바꿔 쓴다. 실제 공장에 붙이면 이 바꾸기만 빼면 된다.
보낼 곳은 backend/.env의 HANDOVER_WEBHOOK_URL(Slack·Teams·Mattermost 등의 Incoming Webhook 주소)에서만 읽고, 출력에는 주소를 쓰지 않는다.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

import openai

from . import analysis, handover
from .db import DataUnavailableError

OUTBOX = Path(__file__).resolve().parents[1] / "local" / "outbox"
WEBHOOK_ENV = "HANDOVER_WEBHOOK_URL"


def just_finished(now: datetime) -> tuple[str, str]:
    """now를 2018년의 같은 날·시각으로 보고, 가장 최근에 끝난 교대를 (날짜, 조)로 돌려준다."""
    try:
        mapped = now.replace(year=2018)
    except ValueError:  # 2월 29일
        mapped = now.replace(year=2018, day=28)
    hour = mapped.hour
    if 6 <= hour < 14:  # 06시에 C조(전날 22시~)가 끝났다.
        return f"{mapped - timedelta(days=1):%Y-%m-%d}", "C"
    if 14 <= hour < 22:
        return f"{mapped:%Y-%m-%d}", "A"
    if hour >= 22:
        return f"{mapped:%Y-%m-%d}", "B"
    return f"{mapped - timedelta(days=1):%Y-%m-%d}", "B"


def send(text: str, url: str, timeout: float = 10) -> None:
    """Incoming Webhook으로 {"text": 마크다운}을 보낸다. Slack·Mattermost·Teams(워크플로) 모두 이 형식을 받는다."""
    body = json.dumps({"text": text}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status >= 300:
                raise RuntimeError(f"메신저가 HTTP {response.status}로 답했습니다.")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"메신저가 HTTP {exc.code}로 답했습니다. 웹훅 주소가 맞는지 확인하세요.") from None
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError("메신저에 연결하지 못했습니다. 웹훅 주소와 네트워크를 확인하세요.") from exc


def run(date: str, shifts: list[str], use_ai: bool, do_send: bool, out_dir: Path = OUTBOX) -> list[Path]:
    client = openai.OpenAI() if use_ai and os.getenv("OPENAI_API_KEY", "").strip() else None
    url = os.getenv(WEBHOOK_ENV, "").strip()
    if do_send and not url:
        raise SystemExit(f"{WEBHOOK_ENV}가 없습니다. backend/.env에 메신저의 Incoming Webhook 주소를 넣으세요.")
    folder = out_dir / date
    folder.mkdir(parents=True, exist_ok=True)
    written = []
    for shift in shifts:
        h = handover.handover(date, shift, client)
        path = folder / f"handover-{date}-{shift}.md"
        path.write_text(h["markdown"] + "\n", encoding="utf-8")
        written.append(path)
        sent = ""
        if do_send:
            send(h["markdown"], url)
            sent = ", 메신저로 보냄"
        print(f"{shift}조 인수인계서 ({h['status']['label']}, {'AI 문장' if h['source'] == 'ai' else '코드 문장'}{sent}): {path}")
    if len(shifts) == len(handover.SHIFTS):
        path = folder / f"energy-report-{date}.md"
        path.write_text(analysis.report_markdown(analysis.daily_report(date)) + "\n", encoding="utf-8")
        written.append(path)
        print(f"일일 보고서: {path}")
    return written


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="교대 인수인계서와 일일 보고서를 자동으로 만듭니다.")
    when = parser.add_mutually_exclusive_group(required=True)
    when.add_argument("--date", help="YYYY-MM-DD (2018년)")
    when.add_argument("--now", action="store_true", help="지금 시각을 2018년으로 바꿔 방금 끝난 교대를 만듭니다 (작업 스케줄러용)")
    parser.add_argument("--shift", choices=list(handover.SHIFTS), help="한 조만 만듭니다. 없으면 A·B·C 모두")
    parser.add_argument("--send", action="store_true", help=f"{WEBHOOK_ENV}로 메신저에 보냅니다")
    parser.add_argument("--no-ai", action="store_true", help="AI 없이 코드가 쓴 문장만 씁니다")
    parser.add_argument("--out", type=Path, default=OUTBOX, help="저장 폴더")
    args = parser.parse_args(argv)

    if args.now:
        date, shift = just_finished(datetime.now())
        shifts = [shift]
    else:
        date, shifts = args.date, [args.shift] if args.shift else list(handover.SHIFTS)
    try:
        run(date, shifts, not args.no_ai, args.send, args.out)
    except (ValueError, RuntimeError, DataUnavailableError) as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
