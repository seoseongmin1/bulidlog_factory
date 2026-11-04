"""경보에 대한 작업자의 대응 기록. 이 컴퓨터의 JSON 파일에만 저장하고, 데이터베이스에는 쓰지 않는다."""
import json
import os
import tempfile
import threading
from datetime import datetime
from pathlib import Path

import pandas as pd

from . import forecast

PATH = Path(__file__).resolve().parents[1] / "local" / "alarm_responses.json"
RESPONSES = {"acted": "조치함", "ignored": "무시함", "false_alarm": "오경보"}
LEVELS = ("warn", "danger")
# 문턱을 올렸을 때 어떻게 되는지 보여 줄 확률(%) 후보
CUTOFF_STEPS = (10, 20, 30, 40, 50)
_lock = threading.Lock()


def _load() -> list[dict]:
    try:
        return json.loads(PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []


def _save(records: list[dict]) -> None:
    PATH.parent.mkdir(parents=True, exist_ok=True)
    # 쓰다 끊겨도 기존 기록이 깨지지 않도록 임시 파일에 쓰고 바꿔 끼운다.
    fd, temp = tempfile.mkstemp(dir=PATH.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as file:
        json.dump(records, file, ensure_ascii=False, indent=1)
    os.replace(temp, PATH)


def record(time: str, target: str, level: str, probability_pct: int, response: str) -> dict:
    """같은 시각·대상의 기록이 있으면 새 응답으로 바꾼다."""
    try:
        stamp = pd.Timestamp(time)
    except ValueError as exc:
        raise ValueError(f"시각 형식이 잘못되었습니다: {time}. YYYY-MM-DD HH:MM으로 입력하세요.") from exc
    if stamp.minute % 15 or stamp.second:
        raise ValueError(f"시각은 15분 단위로 입력하세요. 입력: {time}")
    if target not in forecast.TARGETS:
        raise ValueError(f"대상은 {', '.join(forecast.TARGETS)} 중 하나여야 합니다. 입력: {target}")
    if level not in LEVELS:
        raise ValueError("경보가 켜진 주의·위험 단계에만 기록할 수 있습니다.")
    if response not in RESPONSES:
        raise ValueError(f"응답은 {', '.join(RESPONSES)} 중 하나여야 합니다. 입력: {response}")
    if not 0 <= probability_pct <= 100:
        raise ValueError("확률은 0~100 사이여야 합니다.")
    item = {
        "time": f"{stamp:%Y-%m-%d %H:%M}", "target": target, "level": level,
        "probability_pct": int(probability_pct), "response": response,
        "recorded_at": datetime.now().isoformat(timespec="seconds"),
    }
    with _lock:
        records = [r for r in _load() if (r["time"], r["target"]) != (item["time"], target)]
        records.append(item)
        _save(records)
    return item


def lookup(date: str) -> dict:
    """그날의 기록을 '시각|대상' → 응답으로 돌려준다. 화면이 눌려 있던 버튼을 되살리는 데 쓴다."""
    with _lock:
        return {f"{r['time'][11:]}|{r['target']}": r["response"] for r in _load() if r["time"].startswith(date)}


def stats() -> dict:
    with _lock:
        records = _load()
    warn_cutoffs = forecast.warn_cutoffs_pct()
    by_target = {}
    for target, name in forecast.TARGETS.items():
        mine = [r for r in records if r["target"] == target]
        counts = {key: sum(r["response"] == key for r in mine) for key in RESPONSES}
        judged = counts["acted"] + counts["false_alarm"]
        by_target[target] = {
            "이름": name,
            "건수": len(mine),
            **{RESPONSES[key]: n for key, n in counts.items()},
            "오경보_비율_pct": round(counts["false_alarm"] / judged * 100, 1) if judged else None,
            "현재_주의_문턱_pct": warn_cutoffs[target],
            # 조치함은 필요했던 경보, 오경보는 필요 없던 경보로 본다. 무시함은 맞았는지 알 수 없어 뺀다.
            "문턱_시뮬레이션": [
                {
                    "문턱_pct": step,
                    "사라지는_오경보": sum(r["response"] == "false_alarm" and r["probability_pct"] < step for r in mine),
                    "놓치는_조치": sum(r["response"] == "acted" and r["probability_pct"] < step for r in mine),
                }
                for step in CUTOFF_STEPS
            ],
        }
    hours = sorted({int(r["time"][11:13]) for r in records})
    return {
        "총": len(records),
        "대상별": by_target,
        "시간대별_오경보": [
            {"label": f"{h:02d}시", "value": sum(r["response"] == "false_alarm" and int(r["time"][11:13]) == h for r in records)}
            for h in hours
        ],
        "주의": "이 기록은 이 컴퓨터에만 저장돼요. 경보 기준은 자동으로 바뀌지 않고, 바꾸는 것은 사람이 정해요.",
    }
