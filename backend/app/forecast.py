"""경보 예측. 2018년 데이터의 한 시점을 '지금'으로 보고, 앞으로 1시간 안에 기준을 넘을 확률을 계산한다.

1~7월로 학습하고, 8월로 주의·위험 문턱을 맞추고, 9~12월로 채점한다. 예측 숫자는 이 코드가 계산하며 AI는 쓰지 않는다.
'지금' 뒤의 측정값은 쓰지 않는다. 미래 시각에 대해서는 시간대·요일·휴일 같은 달력 정보만 쓴다.
"""
import sys
from functools import lru_cache

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier

from .analysis import PF_HOURS  # 역률은 09~22시만 본다.
from .data import HOLIDAYS_2018, INTERVALS_PER_HOUR, load_data
from .db import database_url

STEPS_AHEAD = 4  # 15분 × 4 = 1시간
STEPS_PER_DAY = 24 * INTERVALS_PER_HOUR
WARMUP_STEPS = 7 * STEPS_PER_DAY  # 일주일 전 값을 쓰므로 첫 주는 학습에서 뺀다.
LAST_TRAIN_MONTH, CALIBRATION_MONTH = 7, 8
USAGE_QUANTILE = 0.95  # 사용량 기준: 1~8월 15분 수요전력의 상위 5%
PF_LIMIT = 85.0
WARN_RECALL, DANGER_RECALL = 0.8, 0.5  # 8월의 실제 초과 중 이만큼을 잡도록 주의·위험 문턱을 정한다.
PROFILE_DAYS = 28
BASE_DECAY = {"usage": 0.9, "kvarh": 0.85}  # 비교용 기본 모델의 '지금 차이' 감소 비율
LEVELS = {"good": "좋음", "warn": "주의", "danger": "위험"}
TARGETS = {"usage": "사용량", "power_factor": "역률"}
METHODS = {"naive": "단순 (지금 값 유지)", "base": "기본 (평소 + 보정)", "ml": "머신러닝"}
ACTIONS = {
    "usage": {
        "danger": "1시간 안에 기준을 넘을 가능성이 높아요. 큰 설비를 한꺼번에 켜지 말고 순서를 나눠 주세요.",
        "warn": "기준을 넘을 수도 있어요. 큰 설비를 켜기 전에 지금 사용량을 확인해 주세요.",
        "good": "따로 할 일은 없어요.",
    },
    "power_factor": {
        "danger": "전기 효율(역률)이 85% 아래로 떨어질 가능성이 높아요. 콘덴서가 켜져 있는지 확인해 주세요.",
        "warn": "전기 효율(역률)이 떨어질 수 있어요. 콘덴서 상태를 살펴봐 주세요.",
        "good": "따로 할 일은 없어요.",
        "unwatched": "역률은 09~22시만 봐요. 따로 할 일은 없어요.",
    },
}


# 입력 열을 뜻으로 묶은 것. _features의 열 순서와 같다. 경보 이유를 설명할 때 묶음 하나씩 '평소 같은 값'으로 바꿔 본다.
REASON_GROUPS = {
    "recent": ("방금까지의 흐름", list(range(6, 15))),
    "usual": ("평소 패턴", list(range(15, 21))),
    "calendar": ("시간대·요일", list(range(0, 6))),
    "past": ("어제·지난주 같은 시각", list(range(21, 24))),
}
REASON_MIN_POINTS = 3  # 확률을 이만큼(%p) 이상 올린 묶음만 이유로 든다.
REASON_TOP = 3
DAY_TYPE_NAMES = {0: "평일", 1: "토요일", 2: "일요일·공휴일"}


def _power_factor(kwh: np.ndarray, kvarh: np.ndarray) -> np.ndarray:
    apparent = np.hypot(kwh, kvarh)
    return np.where(apparent > 0, kwh / np.where(apparent > 0, apparent, 1) * 100, 100.0)


def _day_type(days: pd.DatetimeIndex) -> np.ndarray:
    """0 평일, 1 토요일, 2 일요일·공휴일"""
    holiday = np.array([d.strftime("%Y-%m-%d") in HOLIDAYS_2018 for d in days])
    dow = np.asarray(days.day_of_week)
    return np.where(holiday | (dow == 6), 2, np.where(dow == 5, 1, 0))


def _profile(values: np.ndarray, day_pos: np.ndarray, slot: np.ndarray, day_types: np.ndarray) -> np.ndarray:
    """각 칸의 '평소 값': 그날 이전 4주 중 같은 날 유형, 같은 15분 칸의 중앙값. 그날 이후 값은 쓰지 않는다."""
    grid = np.full((len(day_types), STEPS_PER_DAY), np.nan)
    grid[day_pos, slot] = values
    usual = np.full_like(grid, np.nanmean(values))
    for i in range(len(day_types)):
        start = max(0, i - PROFILE_DAYS)
        same = np.flatnonzero(day_types[start:i] == day_types[i]) + start
        rows = same if len(same) else np.arange(start, i)
        if len(rows):
            with np.errstate(all="ignore"):
                median = np.nanmedian(grid[rows], axis=0)
            usual[i] = np.where(np.isnan(median), usual[i], median)
    return usual[day_pos, slot]


def _prepare(df: pd.DataFrame) -> dict:
    ts = df.index
    days = ts.normalize()
    unique_days = days.unique()
    day_pos = unique_days.get_indexer(days)
    slot = np.asarray((ts - days) // pd.Timedelta(minutes=15)).astype(int)
    day_types = _day_type(unique_days)
    kwh, kvarh = df["usage_kwh"].to_numpy(float), df["lag_kvarh"].to_numpy(float)
    hour = np.asarray(ts.hour)
    s = {
        "ts": ts,
        "n": len(df),
        "kwh": kwh,
        "kvarh": kvarh,
        "kw": kwh * INTERVALS_PER_HOUR,
        "pf": _power_factor(kwh, kvarh),
        "slot": slot,
        "day_type": day_types[day_pos],
        "dow": np.asarray(ts.day_of_week),
        "daytime": (hour >= PF_HOURS[0]) & (hour < PF_HOURS[1]),
        "month": np.asarray(ts.month),
        "load": df["load_type"].map({"Light": 0, "Medium": 1, "Maximum": 2}).to_numpy(float),
        "usual_kwh": _profile(kwh, day_pos, slot, day_types),
        "usual_kvarh": _profile(kvarh, day_pos, slot, day_types),
    }
    s["usual_pf"] = _power_factor(s["usual_kwh"], s["usual_kvarh"])
    return s


def _features(s: dict, o: np.ndarray) -> np.ndarray:
    """'지금'(o)까지의 측정값과 앞으로 1시간의 달력 정보·평소 값으로 입력을 만든다."""
    past = lambda key, k: s[key][np.maximum(o - k, 0)]  # noqa: E731
    ahead = [o + k for k in range(1, STEPS_AHEAD + 1)]
    columns = [
        s["slot"][o], s["day_type"][ahead[-1]], s["dow"][ahead[-1]], s["daytime"][ahead[0]], s["daytime"][ahead[-1]], s["load"][o],
        *(past("kwh", k) for k in range(4)), past("kvarh", 0), past("kvarh", 1), *(past("pf", k) for k in range(3)),
        s["usual_kwh"][o], s["usual_kvarh"][o],
        np.max([s["usual_kwh"][t] for t in ahead], axis=0), s["usual_kvarh"][ahead[1]], s["usual_kvarh"][ahead[-1]],
        np.min([np.where(s["daytime"][t], s["usual_pf"][t], 100) for t in ahead], axis=0),
        s["kwh"][ahead[-1] - STEPS_PER_DAY], s["kvarh"][ahead[-1] - STEPS_PER_DAY], s["kwh"][ahead[-1] - WARMUP_STEPS],
    ]
    return np.column_stack(columns).astype(float)


def _events(s: dict, o: np.ndarray) -> dict:
    """앞으로 1시간 안에 실제로 기준을 넘었는지"""
    ahead = np.column_stack([o + k for k in range(1, STEPS_AHEAD + 1)])
    return {
        "usage": (s["kw"][ahead] > s["limit_kw"]).any(axis=1),
        "power_factor": ((s["pf"][ahead] < PF_LIMIT) & s["daytime"][ahead]).any(axis=1),
    }


def _comparison_scores(s: dict, o: np.ndarray) -> dict:
    """채점 비교용 점수. 클수록 위험하다."""
    gap_kwh = s["kwh"][o] - s["usual_kwh"][o]
    gap_kvarh = s["kvarh"][o] - s["usual_kvarh"][o]
    base_kw, base_pf = [], []
    for k in range(1, STEPS_AHEAD + 1):
        kwh = s["usual_kwh"][o + k] + gap_kwh * BASE_DECAY["usage"] ** k
        kvarh = s["usual_kvarh"][o + k] + gap_kvarh * BASE_DECAY["kvarh"] ** k
        base_kw.append(kwh * INTERVALS_PER_HOUR)
        base_pf.append(np.where(s["daytime"][o + k], _power_factor(kwh, kvarh), 100))
    return {
        "naive": {"usage": s["kw"][o], "power_factor": -np.where(s["daytime"][o], s["pf"][o], 100)},
        "base": {"usage": np.max(base_kw, axis=0), "power_factor": -np.min(base_pf, axis=0)},
    }


def _cutoffs(score: np.ndarray, event: np.ndarray, calibration: np.ndarray) -> tuple[float, float]:
    positives = score[calibration & event]
    return float(np.quantile(positives, 1 - WARN_RECALL)), float(np.quantile(positives, 1 - DANGER_RECALL))


def load_series() -> dict:
    """데이터베이스에서 읽은 15분 값과 평소 값, 사용량 기준(1~8월 상위 5%)"""
    s = _prepare(load_data())
    s["limit_kw"] = float(np.quantile(s["kw"][s["month"] <= CALIBRATION_MONTH], USAGE_QUANTILE))
    return s


@lru_cache(maxsize=2)
def _model_for(url: str) -> dict:
    """데이터베이스 주소별로 한 번만 학습하고, 모든 시점의 확률을 미리 계산해 둔다."""
    s = load_series()
    o = np.arange(s["n"] - STEPS_AHEAD)
    target_month = s["month"][o + STEPS_AHEAD]
    train = (o >= WARMUP_STEPS) & (target_month <= LAST_TRAIN_MONTH)
    calibration = target_month == CALIBRATION_MONTH
    X, events = _features(s, o), _events(s, o)
    scores = {"ml": {}, **_comparison_scores(s, o)}
    models = {}
    for target, event in events.items():
        model = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, random_state=0)
        model.fit(X[train], event[train])
        models[target] = model
        scores["ml"][target] = model.predict_proba(X)[:, 1]
    cutoffs = {m: {t: _cutoffs(scores[m][t], events[t], calibration) for t in TARGETS} for m in METHODS}
    return {
        "s": s, "origins": o, "events": events, "scores": scores, "cutoffs": cutoffs,
        "X": X, "models": models, "typical": np.median(X[train], axis=0),
    }


def _model() -> dict:
    return _model_for(database_url())


def _level(score: float, cutoffs: tuple[float, float]) -> str:
    return "danger" if score >= cutoffs[1] else "warn" if score >= cutoffs[0] else "good"


def _reason_details(m: dict, target: str, o: int) -> dict:
    """묶음마다 그 시점의 실제 값을 쉬운 문장으로 쓴다. 숫자는 모두 데이터에서 읽은 값이다."""
    s = m["s"]
    prev = max(o - STEPS_AHEAD, 0)
    end = o + STEPS_AHEAD
    clock = f"{s['ts'][end]:%H:%M}"
    calendar = f"{DAY_TYPE_NAMES[int(s['day_type'][end])]} {clock} 무렵이에요."
    if target == "usage":
        kw, before = float(s["kw"][o]), float(s["kw"][prev])
        diff = kw - before
        usual_max = max(float(s["usual_kwh"][o + k]) for k in range(1, STEPS_AHEAD + 1)) * INTERVALS_PER_HOUR
        yesterday = float(s["kw"][max(end - STEPS_PER_DAY, 0)])
        return {
            "recent": f"지금 {kw:.1f}kW로 기준({s['limit_kw']:.1f}kW)의 {kw / s['limit_kw'] * 100:.0f}%예요. 1시간 전보다 {abs(diff):.1f}kW {'늘었어요' if diff >= 0 else '줄었어요'}.",
            "usual": f"앞으로 1시간 평소(지난 4주) 가장 높은 사용량이 {usual_max:.1f}kW예요.",
            "calendar": calendar,
            "past": f"어제 {clock}에는 {yesterday:.1f}kW였어요.",
        }
    pf, before = float(s["pf"][o]), float(s["pf"][prev])
    diff = pf - before
    usual_min = min(float(s["usual_pf"][o + k]) for k in range(1, STEPS_AHEAD + 1) if s["daytime"][o + k])
    y = max(end - STEPS_PER_DAY, 0)
    return {
        "recent": f"지금 역률 {pf:.1f}%이고 1시간 전보다 {abs(diff):.1f}%p {'올랐어요' if diff >= 0 else '내렸어요'}.",
        "usual": f"앞으로 1시간 평소(지난 4주) 가장 낮은 역률이 {usual_min:.1f}%예요.",
        "calendar": calendar,
        "past": f"어제 {clock}에는 역률이 {float(_power_factor(s['kwh'][y], s['kvarh'][y])):.1f}%였어요.",
    }


def _reasons(m: dict, target: str, o: int) -> list[dict]:
    """경보 이유: 입력 묶음 하나를 평소 같은 값(학습 기간의 중앙값)으로 바꿨을 때 확률이 몇 %p 내려가는지로 잰다."""
    row, model = m["X"][o : o + 1], m["models"][target]
    full = float(model.predict_proba(row)[0, 1])
    details = _reason_details(m, target, o)
    found = []
    for key, (name, columns) in REASON_GROUPS.items():
        swapped = row.copy()
        swapped[0, columns] = m["typical"][columns]
        points = round((full - float(model.predict_proba(swapped)[0, 1])) * 100)
        if points >= REASON_MIN_POINTS:
            found.append({"name": name, "points": points, "detail": details[key]})
    return sorted(found, key=lambda r: -r["points"])[:REASON_TOP]


def forecast_at(time: str) -> dict:
    """time(YYYY-MM-DD HH:MM)을 '지금'으로 보고 앞으로 1시간의 사용량·역률 경보를 낸다."""
    m = _model()
    s = m["s"]
    try:
        now = pd.Timestamp(time)
    except ValueError as exc:
        raise ValueError(f"시각 형식이 잘못되었습니다: {time}. YYYY-MM-DD HH:MM으로 입력하세요.") from exc
    first, last = s["ts"][0], s["ts"][len(m["origins"]) - 1]
    if now < first or now > last:
        raise ValueError(f"예측할 수 있는 시각은 {first:%Y-%m-%d %H:%M} ~ {last:%Y-%m-%d %H:%M}입니다. 입력: {time}")
    if now.minute % 15 or now.second:
        raise ValueError(f"시각은 15분 단위(00, 15, 30, 45분)로 입력하세요. 입력: {time}")
    o = int(s["ts"].get_loc(now))
    ml = m["scores"]["ml"]
    watched = bool(s["daytime"][o + 1 : o + STEPS_AHEAD + 1].any())
    usage_level = _level(ml["usage"][o], m["cutoffs"]["ml"]["usage"])
    pf_level = _level(ml["power_factor"][o], m["cutoffs"]["ml"]["power_factor"]) if watched else "good"
    return {
        "time": f"{now:%Y-%m-%d %H:%M}",
        "until": f"{now + pd.Timedelta(hours=1):%Y-%m-%d %H:%M}",
        # 1~8월은 학습과 문턱 맞추기에 쓴 기간이라 실제보다 잘 맞아 보일 수 있다.
        "learned_period": bool(s["month"][o + STEPS_AHEAD] <= CALIBRATION_MONTH),
        "usage": {
            "level": usage_level,
            "label": LEVELS[usage_level],
            "probability_pct": round(float(ml["usage"][o]) * 100),
            "now_kw": round(float(s["kw"][o]), 2),
            "limit_kw": round(s["limit_kw"], 1),
            "action": ACTIONS["usage"][usage_level],
            "reasons": _reasons(m, "usage", o) if usage_level != "good" else [],
        },
        "power_factor": {
            "level": pf_level,
            "label": LEVELS[pf_level],
            "probability_pct": round(float(ml["power_factor"][o]) * 100) if watched else 0,
            "now_pct": round(float(s["pf"][o]), 2),
            "limit_pct": PF_LIMIT,
            "watched": watched,
            "action": ACTIONS["power_factor"][pf_level if watched else "unwatched"],
            "reasons": _reasons(m, "power_factor", o) if watched and pf_level != "good" else [],
        },
    }


def warn_cutoffs_pct() -> dict:
    """지금 쓰는 모델의 '주의' 문턱(%)"""
    cutoffs = _model()["cutoffs"]["ml"]
    return {target: round(cutoffs[target][0] * 100) for target in TARGETS}


def _runs(flags: np.ndarray) -> list[tuple[int, int]]:
    edges = np.diff(np.r_[0, flags.astype(int), 0])
    return list(zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)))


def _grade(s: dict, origins: np.ndarray, score: np.ndarray, cutoff: float, actual: np.ndarray, test: np.ndarray) -> dict:
    """주의 이상 경보를 작업자가 겪는 단위로 센다. 경보가 한 번 켜지면 1번, 초과 구간마다 몇 분 먼저 알렸는지."""
    alarm = np.zeros(s["n"], bool)
    alarm[origins] = score >= cutoff
    actual = actual & test
    leads = []
    episodes = _runs(actual)
    for start, _ in episodes:
        earlier = [k for k in range(STEPS_AHEAD, 0, -1) if start - k >= 0 and alarm[start - k]]
        if earlier:
            leads.append(earlier[0] * 15)
        elif alarm[start]:
            leads.append(0)
    alarms = _runs(alarm & test)
    false = sum(1 for start, end in alarms if not actual[start : end + STEPS_AHEAD + 1].any())
    test_days = len(np.unique(s["ts"][test].normalize()))
    return {
        "초과_구간": len(episodes),
        "미리_잡은_비율_pct": round(len(leads) / len(episodes) * 100, 1) if episodes else None,
        "평균_몇분_전": round(float(np.mean(leads)), 1) if leads else None,
        "30분_이상_먼저_pct": round(float(np.mean(np.array(leads) >= 30)) * 100, 1) if leads else None,
        "경보_횟수": len(alarms),
        "헛경보": false,
        "하루_헛경보": round(false / test_days, 2),
        "경보_켜진_시간_pct": round(float((alarm & test).sum() / test.sum()) * 100, 1),
    }


def backtest() -> dict:
    """9~12월 채점 결과. 세 방식 모두 8월에서 실제 초과의 80%를 잡도록 맞춘 '주의' 문턱으로 센다."""
    m = _model()
    s = m["s"]
    test = s["month"] > CALIBRATION_MONTH
    actual = {"usage": s["kw"] > s["limit_kw"], "power_factor": (s["pf"] < PF_LIMIT) & s["daytime"]}
    return {
        "limit_kw": round(s["limit_kw"], 1),
        "results": {
            target: {
                method: _grade(s, m["origins"], m["scores"][method][target], m["cutoffs"][method][target][0], actual[target], test)
                for method in METHODS
            }
            for target in TARGETS
        },
    }


def day_timeline(date: str) -> dict:
    """하루 96칸의 경보와 실제 값. 그날 기준을 넘은 때마다 경보가 몇 분 먼저 켜졌는지도 센다."""
    m = _model()
    s = m["s"]
    try:
        day = pd.Timestamp(date)
    except ValueError as exc:
        raise ValueError(f"날짜 형식이 잘못되었습니다: {date}. YYYY-MM-DD로 입력하세요.") from exc
    first, last = s["ts"][0].normalize(), s["ts"][-1].normalize()
    if day != day.normalize() or day < first or day > last:
        raise ValueError(f"데이터 범위는 {first.date()} ~ {last.date()}입니다. 입력: {date}")
    days = s["ts"].normalize()
    in_day = np.asarray(days == day)
    ml, cut = m["scores"]["ml"], m["cutoffs"]["ml"]
    usage_over = s["kw"] > s["limit_kw"]
    pf_under = (s["pf"] < PF_LIMIT) & s["daytime"]
    rows = []
    for i in np.flatnonzero(in_day):
        predicted = i < len(m["origins"])
        watched = predicted and bool(s["daytime"][i + 1 : i + STEPS_AHEAD + 1].any())
        rows.append({
            "time": f"{s['ts'][i]:%H:%M}",
            "usage": _level(ml["usage"][i], cut["usage"]) if predicted else None,
            "usage_pct": round(float(ml["usage"][i]) * 100) if predicted else None,
            "kw": round(float(s["kw"][i]), 2),
            "over": bool(usage_over[i]),
            "power_factor": _level(ml["power_factor"][i], cut["power_factor"]) if watched else None,
            "power_factor_pct": round(float(ml["power_factor"][i]) * 100) if watched else None,
            "pf": round(float(s["pf"][i]), 2),
            "under": bool(pf_under[i]),
        })
    summary = {
        target: _grade(s, m["origins"], ml[target], cut[target][0], actual, in_day)
        for target, actual in (("usage", usage_over), ("power_factor", pf_under))
    }
    return {
        "date": f"{day:%Y-%m-%d}",
        "learned_period": day.month <= CALIBRATION_MONTH,
        "limit_kw": round(s["limit_kw"], 1),
        "limit_pct": PF_LIMIT,
        "actions": ACTIONS,
        "rows": rows,
        "summary": summary,
    }


def window_alarms(start: pd.Timestamp, end: pd.Timestamp) -> dict:
    """[start, end) 구간에서 켜진 경보 구간과 실제로 기준을 넘은 구간. 교대 인수인계서에 쓴다.

    구간 시작 시각에 이미 켜져 있던 경보도 이 구간의 경보로 센다. 초과마다 경보가 몇 분 먼저 켜졌는지(lead)를 함께 준다.
    """
    m = _model()
    s = m["s"]
    ts, origins = s["ts"], m["origins"]
    mask = np.asarray((ts >= start) & (ts < end))
    ml, cut = m["scores"]["ml"], m["cutoffs"]["ml"]
    actual = {"usage": s["kw"] > s["limit_kw"], "power_factor": (s["pf"] < PF_LIMIT) & s["daytime"]}
    # 역률은 앞으로 1시간 안에 09~22시가 있는 시점에만 경보를 낸다(forecast_at과 같다).
    watched = np.zeros(s["n"], bool)
    watched[origins] = np.column_stack([s["daytime"][origins + k] for k in range(1, STEPS_AHEAD + 1)]).any(axis=1)
    clock = lambda i: f"{ts[i]:%H:%M}"  # noqa: E731
    result = {"limit_kw": round(s["limit_kw"], 1), "limit_pct": PF_LIMIT}
    for target, name in TARGETS.items():
        prob = np.zeros(s["n"])
        prob[origins] = ml[target]
        level = np.zeros(s["n"], int)
        level[origins] = (ml[target] >= cut[target][0]).astype(int) + (ml[target] >= cut[target][1]).astype(int)
        if target == "power_factor":
            level[~watched] = 0
        alarm = level > 0
        alarms = [
            {
                "시작": clock(a), "끝": f"{ts[b - 1] + pd.Timedelta(minutes=15):%H:%M}", "time": f"{ts[a]:%Y-%m-%d %H:%M}",
                "end_time": f"{ts[b - 1] + pd.Timedelta(minutes=15):%Y-%m-%d %H:%M}",
                "단계": LEVELS["danger" if level[a:b].max() == 2 else "warn"],
                "level": "danger" if level[a:b].max() == 2 else "warn",
                "최고_확률_pct": round(float(prob[a:b].max()) * 100),
                "처음_확률_pct": round(float(prob[a]) * 100),
            }
            for a, b in _runs(alarm & mask)
        ]
        exceed = []
        for a, b in _runs(actual[target] & mask):
            earlier = [k for k in range(STEPS_AHEAD, -1, -1) if a - k >= 0 and alarm[a - k]]
            values = s["kw"][a:b] if target == "usage" else s["pf"][a:b]
            exceed.append({
                "시작": clock(a), "끝": f"{ts[b - 1] + pd.Timedelta(minutes=15):%H:%M}",
                ("최고_kw" if target == "usage" else "최저_pct"): round(float(values.max() if target == "usage" else values.min()), 1),
                "몇분_먼저": earlier[0] * 15 if earlier else None,
            })
        result[target] = {"이름": name, "경보": alarms, "초과": exceed}
    return result


def _period_mask(s: dict, start: str, end: str) -> np.ndarray:
    try:
        a, b = pd.Timestamp(start), pd.Timestamp(end)
    except ValueError as exc:
        raise ValueError(f"날짜 형식이 잘못되었습니다: {start}, {end}. YYYY-MM-DD로 입력하세요.") from exc
    first, last = s["ts"][0].normalize(), s["ts"][-1].normalize()
    if a > b:
        raise ValueError(f"시작일({start})이 종료일({end})보다 늦습니다.")
    if a < first or b > last:
        raise ValueError(f"데이터 범위는 {first.date()} ~ {last.date()}입니다. 입력: {start} ~ {end}")
    days = s["ts"].normalize()
    return np.asarray((days >= a) & (days <= b))


def agent_forecast(time: str) -> dict:
    """에이전트 도구용: 한 시각의 경보와, 같은 모델의 9~12월 채점 결과"""
    return {
        **forecast_at(time),
        "모델_채점_9_12월": scores(),
        "주의": "확률 예측이라 틀릴 수 있다. 헛경보가 있으므로 채점 결과와 함께 말한다.",
    }


def agent_timeline(date: str) -> dict:
    """에이전트 도구용: 하루 96칸 대신 경보가 켜진 구간과 실제로 넘은 구간만 요약하고, 15분 수요전력을 차트로 준다."""
    t = day_timeline(date)
    rows = t["rows"]

    def spans(flag, value_key=None, level_key=None):
        out = []
        for start, end in _runs(np.array([flag(r) for r in rows])):
            part = rows[start:end]
            item = {"시작": part[0]["time"], "끝": f"{pd.Timestamp(t['date'] + ' ' + part[-1]['time']) + pd.Timedelta(minutes=15):%H:%M}"}
            if level_key:
                item["최고_단계"] = LEVELS["danger" if any(r[level_key] == "danger" for r in part) else "warn"]
                item["최고_확률_pct"] = max(r[f"{level_key}_pct"] for r in part)
            if value_key:
                item[f"{value_key}_{'최고' if value_key == 'kw' else '최저'}"] = (max if value_key == "kw" else min)(r[value_key] for r in part)
            out.append(item)
        return out

    return {
        "date": t["date"],
        "학습에_쓴_기간": t["learned_period"],
        "사용량_기준_kw": t["limit_kw"],
        "역률_기준_pct": t["limit_pct"],
        "사용량_경보_구간": spans(lambda r: r["usage"] in ("warn", "danger"), level_key="usage"),
        "사용량_실제_초과_구간": spans(lambda r: r["over"], value_key="kw"),
        "역률_경보_구간": spans(lambda r: r["power_factor"] in ("warn", "danger"), level_key="power_factor"),
        "역률_실제_미달_구간": spans(lambda r: r["under"], value_key="pf"),
        "그날_채점": t["summary"],
        "metric": "peak_kw",
        "rows": [{"label": r["time"], "value": r["kw"]} for r in rows],
    }


def summarize_alarms(start: str, end: str) -> dict:
    """에이전트 도구용: 기간의 경보 횟수, 헛경보, 미리 알린 비율과 시간대별 사용량 경보 칸 수"""
    m = _model()
    s = m["s"]
    mask = _period_mask(s, start, end)
    ml, cut = m["scores"]["ml"], m["cutoffs"]["ml"]
    actual = {"usage": s["kw"] > s["limit_kw"], "power_factor": (s["pf"] < PF_LIMIT) & s["daytime"]}
    predicted = np.zeros(s["n"], bool)
    predicted[m["origins"]] = True
    result = {"기간": f"{start} ~ {end}", "학습에_쓴_기간_포함": bool((s["month"][mask] <= CALIBRATION_MONTH).any())}
    for target in TARGETS:
        level = np.zeros(s["n"], int)
        level[m["origins"]] = (ml[target] >= cut[target][0]).astype(int) + (ml[target] >= cut[target][1]).astype(int)
        result[TARGETS[target]] = {
            **_grade(s, m["origins"], ml[target], cut[target][0], actual[target], mask),
            "위험_칸_수": int(((level == 2) & mask & predicted).sum()),
            "주의_칸_수": int(((level == 1) & mask & predicted).sum()),
        }
    usage_alarm = np.zeros(s["n"], bool)
    usage_alarm[m["origins"]] = ml["usage"] >= cut["usage"][0]
    hours = pd.Series((usage_alarm & mask).astype(int), index=s["ts"]).groupby(s["ts"].hour).sum()
    result["rows_뜻"] = "시간대별로 사용량 경보(주의 이상)가 켜져 있던 15분 칸 수. 경보 횟수가 아니다. 1칸은 15분이다."
    result["rows"] = [{"label": f"{h:02d}시", "value": int(hours.get(h, 0))} for h in range(24)]
    return result


@lru_cache(maxsize=2)
def _scores_for(url: str) -> dict:
    results = backtest()["results"]
    return {target: results[target]["ml"] for target in TARGETS}


def scores() -> dict:
    """화면에 함께 보여 줄, 지금 쓰는 머신러닝 모델의 9~12월 채점 결과"""
    return _scores_for(database_url())


def main() -> None:
    result = backtest()
    print(f"9~12월 채점. 사용량 기준 {result['limit_kw']}kW(1~8월 상위 5%), 역률 기준 {PF_LIMIT}%(09~22시)")
    print("대상 | 방식 | 미리 잡은 초과 | 평균 몇 분 전 | 30분 이상 먼저 | 경보 | 헛경보(하루) | 경보 켜진 시간")
    for target, rows in result["results"].items():
        for method, r in rows.items():
            print(
                f"{TARGETS[target]} | {METHODS[method]} | {r['미리_잡은_비율_pct']}% ({r['초과_구간']}구간) | {r['평균_몇분_전']}분 | "
                f"{r['30분_이상_먼저_pct']}% | {r['경보_횟수']}번 | {r['헛경보']}번 ({r['하루_헛경보']}번) | {r['경보_켜진_시간_pct']}%"
            )


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
