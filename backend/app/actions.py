"""조치 실행 계층: 가상 공장, 허용된 조치, 위험도별 승인, 실행·되돌리기·결과 검증, 감사 기록.

AI가 '말만 하는' 단계를 넘어 실제로 조치하게 하되, 안전장치는 프롬프트가 아니라 이 코드가 강제한다.
- 허용된 조치 4가지만 실행한다. 이름, 입력 형식, 한도(최대 kW, 최대 단수, 시간 범위)를 코드가 검사한다.
- 위험도 낮음(알림, 작업지시서)은 바로 실행하고, 위험도 높음(설비에 닿는 조치)은 사람이 승인해야 실행한다.
- 설비 조치는 실제 공장이 아니라 '가상 공장'에만 한다. 가상 공장은 2018년 실측값 위에 조치의 효과를 더한 것이다.
- 모든 제안·승인·실행·검증은 누가(agent/user/system) 했는지와 함께 기록한다.

시간 규칙: 사건 시각(now)까지의 측정값만 '알고' 있다. 에이전트의 시뮬레이션은 '예상값'(평소값 + 지금의 차이가 줄어드는 형태)으로 하고,
실행 결과의 검증은 그 뒤 실제 2018년 기록에 조치 효과를 더한 값으로 한다. 그래서 '계획대로 됐는지'를 실제 데이터로 채점할 수 있다.
"""
import json
import math
import os
import threading
import uuid
from collections import OrderedDict
from datetime import datetime
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from . import forecast, knowledge
from .data import INTERVALS_PER_HOUR
from .db import database_url

LOCAL = Path(__file__).resolve().parents[1] / "local"
LOG_PATH = LOCAL / "action_log.jsonl"
WORK_ORDER_PATH = LOCAL / "work_orders.json"

HORIZON = 8  # 사건 뒤 2시간(15분 × 8)을 본다. 조치가 문제를 뒤로 밀기만 했는지도 잡으려고 경보(1시간)보다 길게 본다.
SLOT_HOURS = 0.25
CAP_STEP_KVAR = 10
PF_TARGET = 90.0
MAX_ACTIONS = 6
MAX_INCIDENTS = 50
ROLES = ("반장", "에너지 담당", "전기보전팀", "생산관리")
MARGIN_QUANTILE = 0.8
CAP_HEADROOM = 0.8  # 예상 지상 무효전력의 80%까지만 보상한다. 실제 부하가 예상보다 가벼우면 과보상(진상)이 되기 때문이다.

ACTIONS = {
    "defer_load": {
        "이름": "큰 설비 기동 연기",
        "risk": "high",
        "대상": "가상 공장",
        "설명": "start부터 minutes 동안 kw만큼 부하를 줄이고, 그 에너지를 바로 뒤 같은 길이의 시간으로 옮긴다(총 사용량은 같다). "
                "한 칸에서 줄일 수 있는 양은 그 칸 사용량의 절반까지다.",
        "한도": "kw 10~150, minutes 15~60(15분 단위), start는 사건 시각 15분 뒤부터, 옮긴 에너지까지 사건 뒤 2시간 안에 들어가야 함",
    },
    "shed_auxiliary": {
        "이름": "보조 설비 잠시 정지",
        "risk": "high",
        "대상": "가상 공장",
        "설명": "공조, 예열, 집진 보조팬처럼 멈춰도 되는 보조 설비를 minutes 동안 멈춰 kw만큼 줄인다. 미루기와 달리 에너지를 뒤로 옮기지 않는다. "
                "한 칸에서 줄일 수 있는 양은 그 칸 사용량의 30%까지다. 부하가 2시간 내내 높아 미루기가 소용없을 때 쓴다.",
        "한도": "kw 10~40, minutes 15~60(15분 단위), start는 사건 시각 15분 뒤부터",
    },
    "capacitor_step": {
        "이름": "콘덴서 단계 투입",
        "risk": "high",
        "대상": "가상 공장",
        "설명": "start부터 minutes 동안 콘덴서를 steps단(1단 10kVar) 더 넣어 지상 무효전력을 줄인다. 너무 많이 넣으면 진상(과보상)이 된다.",
        "한도": "steps 1~4, minutes 15~120(15분 단위), start는 사건 시각 15분 뒤부터, 사건 뒤 2시간 안",
    },
    "notify": {
        "이름": "담당자 알림",
        "risk": "low",
        "대상": "업무",
        "설명": "역할(to)에게 메신저로 알린다. 웹훅이 없으면 이 컴퓨터의 기록에 남긴다.",
        "한도": f"to는 {', '.join(ROLES)} 중 하나, message는 300자 이하",
    },
    "create_work_order": {
        "이름": "작업지시서 발행",
        "risk": "low",
        "대상": "업무",
        "설명": "담당 역할에게 작업지시서를 만든다. 매뉴얼 출처(cite)를 붙일 수 있다.",
        "한도": f"assignee는 {', '.join(ROLES)} 중 하나, title 80자 이하, due_minutes 15~1440",
    },
}

PLANT_ACTIONS = tuple(k for k, v in ACTIONS.items() if v["대상"] == "가상 공장")
_lock = threading.Lock()
_incidents: OrderedDict[str, dict] = OrderedDict()


# ---------- 기록 ----------

def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _log(incident: dict, who: str, kind: str, text: str, **extra) -> dict:
    step = {"at": _now(), "who": who, "kind": kind, "text": text, **extra}
    incident["steps"].append(step)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _lock, LOG_PATH.open("a", encoding="utf-8") as file:
        file.write(json.dumps({"incident": incident["id"], "time": incident["time"], **step}, ensure_ascii=False) + "\n")
    return step


# ---------- 가상 공장 ----------

def _pf(kwh: np.ndarray, reactive: np.ndarray) -> np.ndarray:
    apparent = np.hypot(kwh, reactive)
    return np.where(apparent > 0, kwh / np.where(apparent > 0, apparent, 1) * 100, 100.0)


def _slot_of(incident: dict, clock: str) -> int:
    """'HH:MM'을 사건 뒤 몇 번째 칸(0 = 사건 15분 뒤)인지로 바꾼다. 자정을 넘는 시각도 다룬다."""
    try:
        h, m = (int(x) for x in clock.split(":"))
    except (ValueError, AttributeError):
        raise ValueError(f"시각은 HH:MM 형식이어야 합니다. 입력: {clock}") from None
    if m % 15 or not (0 <= h < 24 and 0 <= m < 60):
        raise ValueError(f"시각은 15분 단위여야 합니다. 입력: {clock}")
    now = pd.Timestamp(incident["time"])
    target = now.normalize() + pd.Timedelta(hours=h, minutes=m)
    if target <= now:
        # 12시간 안쪽이면 이미 지난 시각으로, 그보다 멀면 자정을 넘긴 다음 날 시각(23:00 사건의 00:15)으로 본다.
        if now - target <= pd.Timedelta(hours=12):
            raise ValueError(f"이미 지난 시각({clock})에는 조치할 수 없습니다. start는 사건 시각 15분 뒤부터입니다.")
        target += pd.Timedelta(days=1)
    return int((target - now) / pd.Timedelta(minutes=15)) - 1


def _window(incident: dict, params: dict, max_minutes: int) -> tuple[int, int]:
    start = _slot_of(incident, params.get("start", ""))
    minutes = params.get("minutes")
    if not isinstance(minutes, (int, float)) or minutes % 15 or not 15 <= minutes <= max_minutes:
        raise ValueError(f"minutes는 15~{max_minutes} 사이의 15분 단위여야 합니다. 입력: {minutes}")
    n = int(minutes) // 15
    if start < 0:
        raise ValueError("이미 지난 시각에는 조치할 수 없습니다. start는 사건 시각 15분 뒤부터입니다.")
    if start + n > HORIZON:
        raise ValueError("조치 시간이 사건 뒤 2시간을 넘습니다.")
    return start, n


def _effect(incident: dict, action: dict, kwh: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """조치 하나가 칸마다 바꾸는 사용량(kWh)과 보상 무효전력(kVar). kwh는 조치를 적용할 기준(예상 또는 실제)이다."""
    d_kwh, comp = np.zeros(HORIZON), np.zeros(HORIZON)
    p = action["params"]
    if action["type"] == "defer_load":
        start, n = _window(incident, p, 60)
        if start + 2 * n > HORIZON:
            raise ValueError("옮긴 에너지가 사건 뒤 2시간을 넘습니다. start를 앞당기거나 minutes를 줄이세요.")
        cut = np.minimum(float(p["kw"]) * SLOT_HOURS, kwh[start : start + n] * 0.5)
        d_kwh[start : start + n] -= cut
        d_kwh[start + n : start + 2 * n] += cut.sum() / n
    elif action["type"] == "shed_auxiliary":
        start, n = _window(incident, p, 60)
        d_kwh[start : start + n] -= np.minimum(float(p["kw"]) * SLOT_HOURS, kwh[start : start + n] * 0.3)
    elif action["type"] == "capacitor_step":
        start, n = _window(incident, p, 120)
        comp[start : start + n] = int(p["steps"]) * CAP_STEP_KVAR
    return d_kwh, comp


def _validate(action_type: str, params: dict, incident: dict) -> dict:
    if action_type not in ACTIONS:
        raise ValueError(f"허용되지 않은 조치입니다: {action_type}. 가능한 조치: {', '.join(ACTIONS)}")
    if not isinstance(params, dict):
        raise ValueError("params는 객체여야 합니다.")
    if action_type == "defer_load":
        kw = params.get("kw")
        if not isinstance(kw, (int, float)) or isinstance(kw, bool) or not 10 <= kw <= 150:
            raise ValueError(f"kw는 10~150 사이여야 합니다. 입력: {kw}")
        clean = {"start": params.get("start"), "minutes": params.get("minutes"), "kw": round(float(kw), 1)}
    elif action_type == "shed_auxiliary":
        kw = params.get("kw")
        if not isinstance(kw, (int, float)) or isinstance(kw, bool) or not 10 <= kw <= 40:
            raise ValueError(f"kw는 10~40 사이여야 합니다. 입력: {kw}")
        clean = {"start": params.get("start"), "minutes": params.get("minutes"), "kw": round(float(kw), 1)}
    elif action_type == "capacitor_step":
        steps = params.get("steps")
        if not isinstance(steps, int) or isinstance(steps, bool) or not 1 <= steps <= 4:
            raise ValueError(f"steps는 1~4 정수여야 합니다. 입력: {steps}")
        clean = {"start": params.get("start"), "minutes": params.get("minutes"), "steps": steps}
    elif action_type == "notify":
        if params.get("to") not in ROLES:
            raise ValueError(f"to는 {', '.join(ROLES)} 중 하나여야 합니다.")
        message = str(params.get("message", "")).strip()
        if not message or len(message) > 300:
            raise ValueError("message는 1~300자여야 합니다.")
        return {"to": params["to"], "message": message}
    else:
        if params.get("assignee") not in ROLES:
            raise ValueError(f"assignee는 {', '.join(ROLES)} 중 하나여야 합니다.")
        title = str(params.get("title", "")).strip()
        due = params.get("due_minutes")
        if not title or len(title) > 80:
            raise ValueError("title은 1~80자여야 합니다.")
        if not isinstance(due, int) or isinstance(due, bool) or not 15 <= due <= 1440:
            raise ValueError("due_minutes는 15~1440 정수여야 합니다.")
        return {"assignee": params["assignee"], "title": title, "due_minutes": due, "cite": str(params.get("cite", ""))[:80]}
    _effect(incident, {"type": action_type, "params": clean}, np.ones(HORIZON))  # 시간 범위 검사
    return clean


def _state(incident: dict, basis: str, include: tuple[str, ...] = ("executed",), extra: dict | None = None) -> dict:
    """basis('expected' 또는 'actual') 위에 include 상태의 조치(와 extra)를 더한 가상 공장의 2시간"""
    kwh = np.array(incident["_base"][basis]["kwh"], float)
    kvarh = np.array(incident["_base"][basis]["kvarh"], float)
    d_kwh, comp = np.zeros(HORIZON), np.zeros(HORIZON)
    for action in [a for a in incident["actions"] if a["status"] in include and a["type"] in PLANT_ACTIONS] + ([extra] if extra else []):
        dk, c = _effect(incident, action, kwh)
        d_kwh += dk
        comp += c
    new_kwh = np.maximum(kwh + d_kwh, 0)
    new_kvarh = np.where(kwh > 0, kvarh * new_kwh / np.where(kwh > 0, kwh, 1), kvarh)
    lag = new_kvarh - comp * SLOT_HOURS
    lead = np.maximum(-lag, 0)
    pf = _pf(new_kwh, np.maximum(lag, 0) + lead)  # 과보상(진상)도 역률을 떨어뜨리는 것으로 본다.
    return {"kwh": new_kwh, "pf": pf, "lead": lead}


def metrics(incident: dict, state: dict) -> dict:
    kw = state["kwh"] * INTERVALS_PER_HOUR
    # 사용량은 2시간 전체를 본다(미룬 부하가 다시 피크를 만드는지). 역률은 경보가 보는 앞 1시간만 본다(콘덴서는 그때 넣는다).
    watched = np.array(incident["_base"]["daytime"], bool) & (np.arange(HORIZON) < HORIZON // 2)
    limit = incident["limits"]["usage_kw"]
    peak = int(np.argmax(kw))
    pf_watch = np.where(watched, state["pf"], 100.0)
    low = int(np.argmin(pf_watch))
    return {
        "peak_kw": round(float(kw[peak]), 1),
        "peak_at": incident["_base"]["times"][peak],
        "over_slots": int((kw > limit).sum()),
        "min_pf": round(float(pf_watch[low]), 1) if watched.any() else None,
        "min_pf_at": incident["_base"]["times"][low] if watched.any() else None,
        "pf_under_slots": int(((state["pf"] < incident["limits"]["pf_pct"]) & watched).sum()),
        "lead_slots": int((state["lead"] > 0.5).sum()),
        "rows": [
            {"time": t, "kw": round(float(k), 1), "pf": round(float(p), 1)}
            for t, k, p in zip(incident["_base"]["times"], kw, state["pf"])
        ],
    }


def _ok(incident: dict, m: dict) -> dict:
    checks = {}
    if "usage" in incident["targets"]:
        checks["usage"] = m["over_slots"] == 0
    if "power_factor" in incident["targets"]:
        checks["power_factor"] = m["pf_under_slots"] == 0 and m["lead_slots"] == 0
    return checks


# ---------- 불확실성 ----------

@lru_cache(maxsize=2)
def _margin_for(url: str) -> float:
    """8월(문턱 맞추기 기간)의 사용량 경보 시점에서, 예상 최대가 실제 최대(앞 1시간)보다 얼마나 낮았는지의 80% 분위(kW).

    15분 공장 부하는 변동이 커서 어떤 예상도 피크를 낮게 본다. 계획할 때 이만큼을 더해 '보수적 최대'로 본다.
    """
    m = forecast._model()
    s = m["s"]
    ml, cut = m["scores"]["ml"]["usage"], m["cutoffs"]["ml"]["usage"]
    origins = m["origins"]
    picks = origins[(ml >= cut[0]) & (s["month"][origins] == forecast.CALIBRATION_MONTH)]
    picks = picks[picks + HORIZON < s["n"]]
    k = np.arange(1, HORIZON // 2 + 1)
    under = []
    for o in picks:
        expected = (s["usual_kwh"][o + k] + (s["kwh"][o] - s["usual_kwh"][o]) * forecast.BASE_DECAY["usage"] ** k) * INTERVALS_PER_HOUR
        under.append(s["kw"][o + k].max() - expected.max())
    return round(float(np.quantile(under, MARGIN_QUANTILE)), 1) if under else 0.0


def planning_margin_kw() -> float:
    return _margin_for(database_url())


# ---------- 사건 ----------

def open_incident(time: str) -> dict:
    """time을 '지금'으로 보고, 경보가 켜진 대상과 앞으로 2시간의 예상·실제 값을 담은 사건을 만든다."""
    f = forecast.forecast_at(time)
    m = forecast._model()
    s = m["s"]
    o = int(s["ts"].get_loc(pd.Timestamp(time)))
    if o + HORIZON >= s["n"]:
        raise ValueError("데이터 마지막 2시간은 조치 결과를 검증할 수 없어 사건으로 만들 수 없습니다.")
    idx = np.arange(o + 1, o + HORIZON + 1)
    gap_kwh, gap_kvarh = s["kwh"][o] - s["usual_kwh"][o], s["kvarh"][o] - s["usual_kvarh"][o]
    k = np.arange(1, HORIZON + 1)
    expected_kwh = np.maximum(s["usual_kwh"][idx] + gap_kwh * forecast.BASE_DECAY["usage"] ** k, 0)
    expected_kvarh = np.maximum(s["usual_kvarh"][idx] + gap_kvarh * forecast.BASE_DECAY["kvarh"] ** k, 0)
    targets = [t for t in forecast.TARGETS if f[t]["level"] != "good"]
    incident = {
        "id": uuid.uuid4().hex[:10],
        "time": f["time"],
        "created_at": _now(),
        "status": "open",
        "planner": None,
        "problem": {"usage": f["usage"], "power_factor": f["power_factor"]},
        "targets": targets,
        "limits": {"usage_kw": round(float(s["limit_kw"]), 1), "pf_pct": forecast.PF_LIMIT},
        "actions": [],
        "steps": [],
        "summary": None,
        "verification": None,
        "_base": {
            "times": [f"{t:%H:%M}" for t in s["ts"][idx]],
            "daytime": [bool(x) for x in s["daytime"][idx]],
            "expected": {"kwh": expected_kwh.tolist(), "kvarh": expected_kvarh.tolist()},
            "actual": {"kwh": s["kwh"][idx].tolist(), "kvarh": s["kvarh"][idx].tolist()},
        },
    }
    names = ", ".join(f"{forecast.TARGETS[t]} {f[t]['label']}({f[t]['probability_pct']}%)" for t in targets)
    _log(incident, "system", "detect", f"{incident['time']} 경보: {names}" if targets else f"{incident['time']}: 켜진 경보가 없어요.")
    if not targets:
        incident["status"] = "no_action"
    with _lock:
        _incidents[incident["id"]] = incident
        while len(_incidents) > MAX_INCIDENTS:
            _incidents.popitem(last=False)
    return incident


def get(incident_id: str) -> dict:
    with _lock:
        if incident_id not in _incidents:
            raise KeyError(f"사건을 찾을 수 없습니다: {incident_id}")
        return _incidents[incident_id]


def overview(incident: dict) -> dict:
    """에이전트에게 주는 사건 요약. 실제 미래 값은 주지 않는다."""
    expected = metrics(incident, _state(incident, "expected", ("executed", "pending")))
    return {
        "지금": incident["time"],
        "경보": {forecast.TARGETS[t]: {k: incident["problem"][t][k] for k in ("label", "probability_pct", "action", "reasons")} for t in incident["targets"]},
        "기준": {"사용량_kw": incident["limits"]["usage_kw"], "역률_pct": incident["limits"]["pf_pct"], "역률_목표_pct": PF_TARGET, "역률_보는_시간": "09~22시"},
        "예상_2시간": expected["rows"],
        "예상_불확실성_kw": planning_margin_kw(),
        "불확실성_뜻": "8월 경보 시점에서 예상 최대가 실제 최대보다 낮았던 정도의 80% 분위다. 예상 최대 + 이 값이 기준 아래여야 안전하다고 본다.",
        "예상_요약": {k: expected[k] for k in ("peak_kw", "peak_at", "over_slots", "min_pf", "min_pf_at", "pf_under_slots", "lead_slots")},
        "조치_목록": {k: {kk: v[kk] for kk in ("이름", "risk", "대상", "설명", "한도")} for k, v in ACTIONS.items()},
        "승인_규칙": "risk=low는 바로 실행되고, risk=high는 작업자가 승인해야 실행된다.",
        "이미_한_조치": [public_action(a) for a in incident["actions"]],
        "남은_조치_수": MAX_ACTIONS - len(incident["actions"]),
        "역할": list(ROLES),
    }


def simulate(incident: dict, action_type: str, params: dict) -> dict:
    """예상값 위에서 조치의 효과를 미리 계산한다. 아무것도 실행하지 않는다."""
    if action_type not in PLANT_ACTIONS:
        raise ValueError(f"시뮬레이션은 설비 조치({', '.join(PLANT_ACTIONS)})만 할 수 있습니다.")
    clean = _validate(action_type, params, incident)
    before = metrics(incident, _state(incident, "expected", ("executed", "pending")))
    after = metrics(incident, _state(incident, "expected", ("executed", "pending"), {"type": action_type, "params": clean}))
    keys = ("peak_kw", "peak_at", "over_slots", "min_pf", "pf_under_slots", "lead_slots")
    _log(incident, "agent", "simulate", f"{describe(action_type, clean)} → 예상 최대 {before['peak_kw']}→{after['peak_kw']}kW"
         + (f", 최저 역률 {before['min_pf']}→{after['min_pf']}%" if before["min_pf"] is not None else "")
         + (f", 과보상 {after['lead_slots']}칸" if after["lead_slots"] else ""), action_type=action_type, params=clean)
    margin = planning_margin_kw()
    return {"조치": action_type, "params": clean, "전": {k: before[k] for k in keys}, "후": {k: after[k] for k in keys},
            "보수적_최대_kw": {"전": round(before["peak_kw"] + margin, 1), "후": round(after["peak_kw"] + margin, 1), "기준": incident["limits"]["usage_kw"]},
            "주의": "예상값(평소 + 지금의 차이) 위의 계산이다. 실제 결과는 실행 뒤 검증한다."}


def describe(action_type: str, p: dict) -> str:
    """조치 입력을 기록에 남길 문장으로"""
    if action_type == "defer_load":
        return f"{p['start']}부터 {p['minutes']}분 동안 {p['kw']}kW 미루기"
    if action_type == "shed_auxiliary":
        return f"{p['start']}부터 {p['minutes']}분 동안 보조 설비 {p['kw']}kW 정지"
    if action_type == "capacitor_step":
        return f"{p['start']}부터 {p['minutes']}분 동안 콘덴서 {p['steps']}단 추가"
    return ""


def public_action(a: dict) -> dict:
    return {k: v for k, v in a.items() if not k.startswith("_")}


def propose(incident: dict, action_type: str, params: dict, reason: str, actor: str = "agent") -> dict:
    """조치를 제안한다. risk=low는 바로 실행하고, risk=high는 승인 대기로 둔다. 한도를 넘으면 거절한다."""
    if len(incident["actions"]) >= MAX_ACTIONS:
        raise ValueError(f"한 사건에서 조치는 {MAX_ACTIONS}개까지입니다.")
    clean = _validate(action_type, params, incident)
    spec = ACTIONS[action_type]
    action = {
        "id": f"A{len(incident['actions']) + 1}",
        "type": action_type,
        "name": spec["이름"],
        "risk": spec["risk"],
        "target": spec["대상"],
        "params": clean,
        "reason": str(reason or "").strip()[:300],
        "proposed_by": actor,
        "status": "pending",
        "expected": None,
        "result": None,
    }
    if action_type in PLANT_ACTIONS:
        before = metrics(incident, _state(incident, "expected", ("executed", "pending")))
        after = metrics(incident, _state(incident, "expected", ("executed", "pending"), action))
        action["expected"] = {"peak_kw": [before["peak_kw"], after["peak_kw"]], "min_pf": [before["min_pf"], after["min_pf"]],
                              "lead_slots": after["lead_slots"]}
    incident["actions"].append(action)
    _log(incident, actor, "propose", f"{action['id']} {spec['이름']} 제안: {action['reason']}", action_id=action["id"])
    if spec["risk"] == "low":
        _execute(incident, action, "system")
    else:
        incident["status"] = "waiting_approval"
    return public_action(action)


def _deliver(action: dict, incident: dict) -> dict:
    p = action["params"]
    if action["type"] == "notify":
        text = f"[공장 에너지 에이전트] {incident['time']} → {p['to']}: {p['message']}"
        url = os.getenv("HANDOVER_WEBHOOK_URL", "").strip()
        if url:
            from .autorun import send

            try:
                send(text, url)
                return {"전달": "메신저", "text": text}
            except RuntimeError as exc:
                return {"전달": "실패", "오류": str(exc), "text": text}
        return {"전달": "이 컴퓨터 기록(웹훅 없음)", "text": text}
    with _lock:
        try:
            orders = json.loads(WORK_ORDER_PATH.read_text(encoding="utf-8"))
        except FileNotFoundError:
            orders = []
        order = {
            "id": f"WO-{len(orders) + 1:04d}", "incident": incident["id"], "incident_time": incident["time"],
            "title": p["title"], "assignee": p["assignee"], "due_minutes": p["due_minutes"], "cite": p["cite"],
            "status": "열림", "created_at": _now(),
        }
        orders.append(order)
        WORK_ORDER_PATH.parent.mkdir(parents=True, exist_ok=True)
        WORK_ORDER_PATH.write_text(json.dumps(orders, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"작업지시서": order["id"]}


def _execute(incident: dict, action: dict, actor: str) -> None:
    action["status"] = "executed"
    action["executed_by"] = actor
    action["executed_at"] = _now()
    if action["type"] in ("notify", "create_work_order"):
        action["result"] = _deliver(action, incident)
        _log(incident, actor, "execute", f"{action['id']} {action['name']} 실행: {action['result'].get('작업지시서') or action['result']['전달']}",
             action_id=action["id"])
    else:
        _log(incident, actor, "execute", f"{action['id']} {action['name']} 가상 공장에 실행", action_id=action["id"])
        verify(incident)
    _refresh_status(incident)


def _refresh_status(incident: dict) -> None:
    if any(a["status"] == "pending" for a in incident["actions"]):
        incident["status"] = "waiting_approval"
    elif incident["verification"] is not None:
        incident["status"] = "resolved" if incident["verification"]["resolved"] else "unresolved"
    elif incident["targets"]:
        incident["status"] = "open"


def decide(incident: dict, action_id: str, approve: bool, who: str = "user") -> dict:
    action = next((a for a in incident["actions"] if a["id"] == action_id), None)
    if action is None:
        raise KeyError(f"조치를 찾을 수 없습니다: {action_id}")
    if action["status"] != "pending":
        raise ValueError(f"승인 대기 중인 조치가 아닙니다 ({action['status']}).")
    if approve:
        _log(incident, who, "approve", f"{action_id} {action['name']} 승인", action_id=action_id)
        _execute(incident, action, who)
    else:
        action["status"] = "rejected"
        _log(incident, who, "reject", f"{action_id} {action['name']} 거절", action_id=action_id)
        _refresh_status(incident)
    return public_action(action)


def revert(incident: dict, action_id: str, who: str = "user") -> dict:
    """가상 공장 조치를 되돌린다. 알림과 작업지시서는 이미 나갔으므로 되돌릴 수 없다."""
    action = next((a for a in incident["actions"] if a["id"] == action_id), None)
    if action is None:
        raise KeyError(f"조치를 찾을 수 없습니다: {action_id}")
    if action["status"] != "executed" or action["type"] not in PLANT_ACTIONS:
        raise ValueError("실행된 가상 공장 조치만 되돌릴 수 있습니다.")
    action["status"] = "reverted"
    _log(incident, who, "revert", f"{action_id} {action['name']} 되돌림", action_id=action_id)
    verify(incident)
    _refresh_status(incident)
    return public_action(action)


def verify(incident: dict) -> dict:
    """실제 2018년 기록 + 실행된 조치로 결과를 채점한다. 조치하지 않았을 때(실제 기록 그대로)와 비교한다."""
    without = metrics(incident, _state(incident, "actual", ()))
    with_actions = metrics(incident, _state(incident, "actual"))
    checks = _ok(incident, with_actions)
    needed = {t: not v for t, v in _ok(incident, without).items()}
    result = {
        "resolved": all(checks.values()) if checks else True,
        "checks": checks,
        "needed": needed,
        "without": {k: v for k, v in without.items()},
        "with": {k: v for k, v in with_actions.items()},
    }
    incident["verification"] = result
    parts = []
    if "usage" in checks:
        parts.append(f"최대 {without['peak_kw']}→{with_actions['peak_kw']}kW (기준 {incident['limits']['usage_kw']}kW, 넘은 칸 {with_actions['over_slots']})")
    if "power_factor" in checks and with_actions["min_pf"] is not None:
        parts.append(f"최저 역률 {without['min_pf']}→{with_actions['min_pf']}% (진상 칸 {with_actions['lead_slots']})")
    if not any(needed.values()):
        parts.append("실제로는 조치하지 않았어도 기준을 넘지 않았어요(헛경보)")
    text = ("해결됨: " if result["resolved"] else "아직 해결 안 됨: ") + ", ".join(parts)
    last = next((step for step in reversed(incident["steps"]) if step["kind"] == "verify"), None)
    if last is None or last["text"] != text:
        _log(incident, "system", "verify", text, resolved=result["resolved"])
    return result


def public(incident: dict) -> dict:
    """화면에 줄 사건. 예상·조치 전후 그래프 값을 함께 준다."""
    expected = metrics(incident, _state(incident, "expected", ("executed", "pending")))
    return {
        **{k: v for k, v in incident.items() if not k.startswith("_") and k != "actions"},
        "actions": [public_action(a) for a in incident["actions"]],
        "expected": expected,
        "catalog": {k: {kk: v[kk] for kk in ("이름", "risk", "대상")} for k, v in ACTIONS.items()},
    }


def work_orders() -> list[dict]:
    try:
        return json.loads(WORK_ORDER_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []


# ---------- 규칙 계획자 (API 키가 없거나 AI가 실패할 때) ----------

def _clock(incident: dict, slot: int) -> str:
    return incident["_base"]["times"][slot]


def rule_plan(incident: dict) -> None:
    """같은 조치 목록과 같은 안전장치를 쓰는 코드 계획자. AI 계획자와 비교하는 기준선이기도 하다."""
    incident["planner"] = incident["planner"] or "rule"
    limit = incident["limits"]["usage_kw"]
    hour = HORIZON // 2
    if "usage" in incident["targets"]:
        exp = metrics(incident, _state(incident, "expected", ("executed", "pending")))
        kw = np.array([r["kw"] for r in exp["rows"]])
        margin = planning_margin_kw()
        peak = float(kw[:hour].max())
        later = float(kw[hour:].max())
        need = peak + margin - limit
        if need <= 0:
            _log(incident, "agent", "think", f"보수적으로 봐도 앞 1시간 최대 {peak + margin:.1f}kW로 기준 아래라 설비 조치는 하지 않아요.")
            message = f"사용량 경보({incident['problem']['usage']['label']})가 켜졌어요. 큰 설비를 켜기 전에 지금 사용량을 확인해 주세요."
        elif later + margin + need <= limit:
            cut = int(min(150, max(20, math.ceil(need / 10) * 10)))
            _log(incident, "agent", "think",
                 f"앞 1시간 예상 최대 {peak:.1f}kW + 불확실성 {margin}kW = {peak + margin:.1f}kW로 기준({limit}kW)을 넘을 수 있어요. "
                 f"다음 1시간은 여유가 있어서({later:.1f}kW) 1시간 내내 {cut}kW를 줄이고 그 뒤로 옮겨요.")
            propose(incident, "defer_load", {"start": _clock(incident, 0), "minutes": 60, "kw": cut},
                    f"앞 1시간 수요전력이 기준({limit}kW)을 넘을 수 있어 큰 설비 기동을 1시간 미룹니다.", "agent")
            message = f"{_clock(incident, 0)}부터 1시간 동안 큰 설비 기동을 미뤄 {cut}kW를 줄여 주세요. [SOP-EN-04 3. 사용량 경보가 켜져 있을 때]"
        else:
            shed = int(min(40, max(10, math.ceil(need / 10) * 10)))
            _log(incident, "agent", "think",
                 f"앞 1시간 보수적 최대 {peak + margin:.1f}kW, 다음 1시간도 {later + margin:.1f}kW라 미루면 뒤에서 다시 넘어요. "
                 f"옮기지 않는 보조 설비 정지({shed}kW)를 쓰고, 부족한 {max(0, need - shed):.0f}kW는 생산 일정 조정이 필요해 에너지 담당에게 넘겨요.")
            propose(incident, "shed_auxiliary", {"start": _clock(incident, 0), "minutes": 60, "kw": shed},
                    "부하가 2시간 내내 높아 미루기 대신 보조 설비를 잠시 멈춥니다. [SOP-EN-01 2. 경보 단계별 조치]", "agent")
            if need > shed:
                propose(incident, "notify", {"to": "에너지 담당", "message": f"{incident['time']} 사용량 경보: 보조 설비 정지({shed}kW)로는 "
                                             f"부족할 수 있어요(필요 약 {need:.0f}kW). 생산 일정 조정을 검토해 주세요."},
                        "에이전트가 할 수 있는 조치의 한도를 넘어 사람의 판단이 필요합니다.", "agent")
            message = f"{_clock(incident, 0)}부터 1시간 동안 공조·예열·집진 보조팬 등 보조 설비를 잠시 멈춰 주세요. [SOP-EN-01 2. 경보 단계별 조치]"
        propose(incident, "notify", {"to": "반장", "message": message}, "현장 반장이 경보와 조치를 알아야 합니다.", "agent")
    if "power_factor" in incident["targets"]:
        exp = metrics(incident, _state(incident, "expected", ("executed", "pending")))
        kwh = np.array(incident["_base"]["expected"]["kwh"])
        kvarh = np.array(incident["_base"]["expected"]["kvarh"])
        allowed = kwh * math.tan(math.acos(PF_TARGET / 100))
        low = [i for i in range(hour) if incident["_base"]["daytime"][i] and kvarh[i] > allowed[i]]
        if low:
            first, last = low[0], low[-1] + 1
            need = max((kvarh[i] - allowed[i]) / SLOT_HOURS for i in low)
            steps = int(min(4, max(1, math.ceil(need / CAP_STEP_KVAR))))
            floor = float(kvarh[first:last].min()) / SLOT_HOURS * CAP_HEADROOM
            while steps > 0 and steps * CAP_STEP_KVAR > floor:
                steps -= 1
            _log(incident, "agent", "think",
                 f"{_clock(incident, first)}~{_clock(incident, last - 1)} 예상 역률이 90% 아래예요. 최대 {need:.0f}kVar가 필요하지만 "
                 f"과보상을 피하려고 예상 무효전력의 80%({floor:.0f}kVar)를 넘지 않게 {steps}단으로 정했어요.")
            if steps:
                propose(incident, "capacitor_step", {"start": _clock(incident, first), "minutes": 15 * (last - first), "steps": steps},
                        f"역률이 {incident['limits']['pf_pct']}% 아래로 떨어질 수 있어 콘덴서를 {steps}단 더 넣습니다.", "agent")
        doc = knowledge.for_alarm("power_factor", 1)
        propose(incident, "create_work_order", {"assignee": "전기보전팀", "title": "자동 역률 조정기 단계·콘덴서 상태 점검",
                                                "due_minutes": 60, "cite": doc[0]["cite"] if doc else ""},
                "역률 경보가 반복되면 조정기 설정이나 콘덴서 용량을 점검해야 합니다.", "agent")
    if incident["status"] != "waiting_approval":
        verify(incident)
        _refresh_status(incident)
    waiting = incident["status"] == "waiting_approval"
    _log(incident, "agent", "finish", "조치 계획을 마쳤어요. 설비 조치는 작업자 승인을 기다려요." if waiting else "조치 계획을 마쳤어요.")
    incident["summary"] = incident["summary"] or "규칙 계획자가 경보 종류에 맞는 기본 조치를 제안했어요."


# ---------- 정책 채점 (python -m app.actions) ----------

def backtest(step: int = 4) -> dict:
    """9~12월 사용량 경보 시점(step칸마다 하나)에서 조치 정책을 실제 기록으로 채점한다.

    '무조건 미루기'는 사람이 흔히 떠올리는 조치라 비교 기준으로 둔다. 규칙 계획자는 이 채점으로 골랐다.
    """
    global LOG_PATH, WORK_ORDER_PATH
    saved = LOG_PATH, WORK_ORDER_PATH
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="actions-backtest-"))
    LOG_PATH, WORK_ORDER_PATH = tmp / "log.jsonl", tmp / "work_orders.json"
    try:
        m = forecast._model()
        s = m["s"]
        ml, cut = m["scores"]["ml"]["usage"], m["cutoffs"]["ml"]["usage"]
        origins = m["origins"]
        picks = [int(o) for o in origins[(s["month"][origins] > forecast.CALIBRATION_MONTH) & (ml >= cut[0])] if o % step == 0 and o + HORIZON < s["n"]]
        results = {}
        for policy in ("none", "defer", "rule"):
            r = {"사건": 0, "실제로_넘은_사건": 0, "완전_해결": 0, "악화": 0, "넘은_칸_전": 0, "넘은_칸_후": 0, "초과_kwh_전": 0.0, "초과_kwh_후": 0.0}
            for o in picks:
                incident = open_incident(f"{s['ts'][o]:%Y-%m-%d %H:%M}")
                if "usage" not in incident["targets"]:
                    continue
                incident["targets"] = ["usage"]  # 사용량 정책만 비교한다.
                if policy == "rule":
                    rule_plan(incident)
                elif policy == "defer":
                    exp = metrics(incident, _state(incident, "expected", ()))
                    need = max(r_["kw"] for r_ in exp["rows"][: HORIZON // 2]) + planning_margin_kw() - incident["limits"]["usage_kw"]
                    if need > 0:
                        propose(incident, "defer_load", {"start": _clock(incident, 0), "minutes": 60,
                                                         "kw": int(min(150, max(20, math.ceil(need / 10) * 10)))}, "비교", "system")
                for a in incident["actions"]:
                    if a["status"] == "pending":
                        decide(incident, a["id"], True, "system")
                v = verify(incident)
                limit = incident["limits"]["usage_kw"]
                r["사건"] += 1
                r["실제로_넘은_사건"] += v["needed"]["usage"]
                r["완전_해결"] += v["needed"]["usage"] and v["checks"]["usage"]
                r["악화"] += v["with"]["over_slots"] > v["without"]["over_slots"]
                r["넘은_칸_전"] += v["without"]["over_slots"]
                r["넘은_칸_후"] += v["with"]["over_slots"]
                r["초과_kwh_전"] += sum(max(x["kw"] - limit, 0) for x in v["without"]["rows"]) * SLOT_HOURS
                r["초과_kwh_후"] += sum(max(x["kw"] - limit, 0) for x in v["with"]["rows"]) * SLOT_HOURS
                with _lock:
                    _incidents.pop(incident["id"], None)
            r["초과_kwh_전"], r["초과_kwh_후"] = round(r["초과_kwh_전"], 1), round(r["초과_kwh_후"], 1)
            results[policy] = r
        return {"불확실성_kw": planning_margin_kw(), "결과": results}
    finally:
        LOG_PATH, WORK_ORDER_PATH = saved


def main() -> None:
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    result = backtest()
    names = {"none": "조치 안 함", "defer": "무조건 미루기", "rule": "규칙 계획자"}
    print(f"9~12월 사용량 경보 사건 채점 (계획용 불확실성 {result['불확실성_kw']}kW, 실제 2018년 기록 + 조치 효과)")
    print("정책 | 사건 | 실제로 넘은 사건 | 완전 해결 | 오히려 악화 | 넘은 15분 칸 | 기준 초과 에너지")
    for key, r in result["결과"].items():
        change = (r["초과_kwh_후"] / r["초과_kwh_전"] - 1) * 100 if r["초과_kwh_전"] else 0
        print(f"{names[key]} | {r['사건']} | {r['실제로_넘은_사건']} | {r['완전_해결']} | {r['악화']} | "
              f"{r['넘은_칸_전']}→{r['넘은_칸_후']} | {r['초과_kwh_전']:,}→{r['초과_kwh_후']:,}kWh ({change:+.0f}%)")


if __name__ == "__main__":
    main()
