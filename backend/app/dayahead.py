"""내일 예측. 하루 전까지의 데이터로, 다음 날 2시간 구간마다 사용량 기준(408.1kW)을 넘을 가능성을 '위험 시간대'로 알린다.

구간마다 지난 4주 같은 날 유형(평일, 토요일, 일요일·공휴일)에서 그 구간이 기준을 넘은 날의 비율을 센다.
8월로 주의·위험 문턱을 맞추고 9~12월로 채점한다. 예측할 날의 측정값은 쓰지 않는다.
머신러닝(HistGradientBoosting)도 시험했으나 이 방식보다 나아지지 않아 쓰지 않는다(docs/spec.md 기능 10).
예측 숫자는 이 코드가 계산하며 AI는 쓰지 않는다.
"""
import sys
from functools import lru_cache

import numpy as np
import pandas as pd

from . import forecast
from .data import INTERVALS_PER_HOUR
from .db import database_url

STEPS_PER_DAY = forecast.STEPS_PER_DAY
BLOCK_STEPS = 8  # 2시간
BLOCKS_PER_DAY = STEPS_PER_DAY // BLOCK_STEPS
WARMUP_DAYS = 7  # 첫 주는 비교할 지난 날이 모자라 예측하지 않는다.
LOOKBACK_DAYS = forecast.PROFILE_DAYS
LEVELS = forecast.LEVELS
METHODS = {"naive": "단순 (어제 같은 구간이 넘었으면)", "freq": "지난 4주 같은 날 유형의 빈도"}
ACTIONS = {
    "danger": "큰 설비를 한꺼번에 켜지 않도록 가동 순서를 미리 나눠 두세요.",
    "warn": "이 시간 전에 큰 설비의 가동 계획을 확인해 주세요.",
}
NONE_NOTE = "기준을 넘은 적이 잦은 시간대가 없어요. 따로 준비할 일은 없어요."
SAME_DAY_NOTE = "당일에는 경보 예측 탭이 1시간 전에 다시 알려 줘요."


def _block_label(block: int) -> tuple[str, str]:
    start, end = block * BLOCK_STEPS * 15, (block + 1) * BLOCK_STEPS * 15
    return f"{start // 60:02d}:{start % 60:02d}", f"{end // 60:02d}:{end % 60:02d}"


@lru_cache(maxsize=2)
def _model_for(url: str) -> dict:
    s = forecast.load_series()
    if s["n"] % STEPS_PER_DAY or s["ts"][0] != s["ts"][0].normalize():
        raise ValueError("하루 단위로 나뉘는 데이터가 아닙니다.")
    days = s["n"] // STEPS_PER_DAY
    day_type, month = s["day_type"][::STEPS_PER_DAY], s["month"][::STEPS_PER_DAY]
    kw = s["kw"].reshape(days, STEPS_PER_DAY)
    over = (kw > s["limit_kw"]).reshape(days, BLOCKS_PER_DAY, BLOCK_STEPS).any(axis=2)

    # 구간마다, 그날 이전 4주 중 같은 날 유형에서 기준을 넘은 날의 비율과 비교한 날 수
    freq = np.zeros((days, BLOCKS_PER_DAY))
    compared = np.zeros(days, int)
    exceeded = np.zeros((days, BLOCKS_PER_DAY), int)
    for d in range(WARMUP_DAYS, days):
        recent = np.arange(max(0, d - LOOKBACK_DAYS), d)
        same = recent[day_type[recent] == day_type[d]]
        same = same if len(same) else recent
        compared[d] = len(same)
        exceeded[d] = over[same].sum(axis=0)
        freq[d] = over[same].mean(axis=0)

    valid = np.arange(days) >= WARMUP_DAYS
    calibration = valid & (month == forecast.CALIBRATION_MONTH)
    positives = freq[calibration][over[calibration]]
    cutoffs = (
        float(np.quantile(positives, 1 - forecast.WARN_RECALL)),
        float(np.quantile(positives, 1 - forecast.DANGER_RECALL)),
    )
    naive = np.zeros_like(over)
    naive[1:] = over[:-1]
    return {
        "s": s, "days": days, "kw": kw, "over": over, "freq": freq, "compared": compared, "exceeded": exceeded,
        "cutoffs": cutoffs, "naive": naive, "valid": valid, "month": month,
    }


def _model() -> dict:
    return _model_for(database_url())


def _level(frequency: float, cutoffs: tuple[float, float]) -> str:
    return "danger" if frequency >= cutoffs[1] else "warn" if frequency >= cutoffs[0] else "good"


def _grade(flag: np.ndarray, over: np.ndarray, test: np.ndarray) -> dict:
    flag, over = flag[test], over[test]
    hit = int((flag & over).sum())
    return {
        "초과_구간": int(over.sum()),
        "미리_잡은_비율_pct": round(hit / over.sum() * 100, 1) if over.sum() else None,
        "하루_켜진_구간": round(float(flag.sum() / len(flag)), 2),
        "하루_헛_구간": round(float((flag & ~over).sum() / len(flag)), 2),
        "켜진_구간_중_실제_pct": round(hit / flag.sum() * 100, 1) if flag.sum() else None,
        "구간_켜진_비율_pct": round(float(flag.mean()) * 100, 1),
    }


def backtest() -> dict:
    """9~12월 채점. 주의 이상(문턱 이상)과 위험(더 높은 문턱 이상)을 따로 센다. 2시간 구간 하나가 한 칸이다."""
    m = _model()
    test = m["valid"] & (m["month"] > forecast.CALIBRATION_MONTH)
    # 처음 일주일을 빼고, 아무 구간이나 켰을 때의 비율(기준선)도 함께 둔다.
    base_rate = float(m["over"][test].mean())
    return {
        "limit_kw": round(m["s"]["limit_kw"], 1),
        "문턱_빈도": {"warn": round(m["cutoffs"][0], 3), "danger": round(m["cutoffs"][1], 3)},
        "아무_구간이나_초과_pct": round(base_rate * 100, 1),
        "results": {
            "naive": _grade(m["naive"], m["over"], test),
            "freq_warn": _grade(m["freq"] >= m["cutoffs"][0], m["over"], test),
            "freq_danger": _grade(m["freq"] >= m["cutoffs"][1], m["over"], test),
        },
    }


@lru_cache(maxsize=2)
def _scores_for(url: str) -> dict:
    return backtest()


def scores() -> dict:
    return _scores_for(database_url())


def day_ahead(date: str) -> dict:
    """date(다음 날)의 2시간 구간별 위험 정도와 실제 결과. 예측에는 date 이전 값만 쓴다."""
    m = _model()
    s = m["s"]
    try:
        day = pd.Timestamp(date)
    except ValueError as exc:
        raise ValueError(f"날짜 형식이 잘못되었습니다: {date}. YYYY-MM-DD로 입력하세요.") from exc
    first = s["ts"][0].normalize() + pd.Timedelta(days=WARMUP_DAYS)
    last = s["ts"][-1].normalize()
    if day != day.normalize() or day < first or day > last:
        raise ValueError(f"예측할 수 있는 날짜는 {first.date()} ~ {last.date()}입니다. 입력: {date}")
    d = int((day - s["ts"][0].normalize()).days)
    blocks, windows = [], []
    for b in range(BLOCKS_PER_DAY):
        start, end = _block_label(b)
        level = _level(m["freq"][d, b], m["cutoffs"])
        actual_max = float(m["kw"][d, b * BLOCK_STEPS : (b + 1) * BLOCK_STEPS].max())
        blocks.append({
            "start": start, "end": end, "level": level, "label": LEVELS[level],
            "exceeded_days": int(m["exceeded"][d, b]), "compared_days": int(m["compared"][d]),
            "over": bool(m["over"][d, b]), "actual_max_kw": round(actual_max, 1),
        })
        if level != "good":
            windows.append(blocks[-1])
    plan = [f"{w['start']}~{w['end']} {w['label']}: {ACTIONS[w['level']]}" for w in windows]
    flagged = np.array([b["level"] != "good" for b in blocks])
    over = np.array([b["over"] for b in blocks])
    usual = (s["usual_kwh"] * INTERVALS_PER_HOUR).reshape(m["days"], STEPS_PER_DAY)[d]
    rows = [
        {"time": f"{s['ts'][i]:%H:%M}", "usual_kw": round(float(usual[i - d * STEPS_PER_DAY]), 1), "kw": round(float(s["kw"][i]), 2)}
        for i in range(d * STEPS_PER_DAY, (d + 1) * STEPS_PER_DAY)
    ]
    return {
        "date": f"{day:%Y-%m-%d}",
        "issued_at": f"{day - pd.Timedelta(days=1):%Y-%m-%d} 23:45",
        "learned_period": day.month <= forecast.CALIBRATION_MONTH,
        "limit_kw": round(s["limit_kw"], 1),
        "day_type": forecast.DAY_TYPE_NAMES[int(m["s"]["day_type"][d * STEPS_PER_DAY])],
        "blocks": blocks,
        "plan": plan or [NONE_NOTE],
        "same_day_note": SAME_DAY_NOTE,
        "result": {
            "위험_구간": int(flagged.sum()),
            "실제_초과_구간": int(over.sum()),
            "맞힌_구간": int((flagged & over).sum()),
            "놓친_구간": int((~flagged & over).sum()),
            "헛_구간": int((flagged & ~over).sum()),
        },
        "rows": rows,
    }


def agent_day_ahead(date: str) -> dict:
    """에이전트 도구용: 위험 시간대와 계획, 그날의 결과와 9~12월 채점. 평소 사용량은 차트로 준다."""
    result = day_ahead(date)
    return {
        "date": result["date"],
        "예측_시점": result["issued_at"],
        "학습에_쓴_기간": result["learned_period"],
        "사용량_기준_kw": result["limit_kw"],
        "날_유형": result["day_type"],
        "위험_시간대": [
            {
                "구간": f"{b['start']}~{b['end']}", "단계": b["label"],
                "지난_같은_날유형_중_넘은_날": f"{b['compared_days']}일 중 {b['exceeded_days']}일",
                "실제로_넘음": b["over"], "실제_최고_kw": b["actual_max_kw"],
            }
            for b in result["blocks"]
            if b["level"] != "good"
        ],
        "계획": result["plan"],
        "그날_결과": result["result"],
        "모델_채점_9_12월": scores(),
        "주의": "확률이 아니라 지난 4주 같은 날 유형에서 기준을 넘은 날의 비율로 본 위험 시간대다. 맞힌 비율이 낮아 헛경보가 많으니 채점 결과와 함께 말한다.",
        "metric": "peak_kw",
        "rows": [{"label": r["time"], "value": r["usual_kw"]} for r in result["rows"]],
    }


def main() -> None:
    result = backtest()
    print(f"9~12월 채점. 사용량 기준 {result['limit_kw']}kW, 아무 2시간 구간이나 넘은 비율 {result['아무_구간이나_초과_pct']}%")
    print(f"문턱(지난 같은 날 유형 중 넘은 날의 비율): 주의 {result['문턱_빈도']['warn']}, 위험 {result['문턱_빈도']['danger']}")
    print("방식 | 미리 잡은 초과 | 하루 켜진 구간 | 하루 헛 구간 | 켜진 구간 중 실제 | 켜진 비율")
    names = {"naive": METHODS["naive"], "freq_warn": "빈도 (주의 이상)", "freq_danger": "빈도 (위험)"}
    for key, r in result["results"].items():
        print(
            f"{names[key]} | {r['미리_잡은_비율_pct']}% ({r['초과_구간']}구간) | {r['하루_켜진_구간']}개 | {r['하루_헛_구간']}개 | "
            f"{r['켜진_구간_중_실제_pct']}% | {r['구간_켜진_비율_pct']}%"
        )


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
