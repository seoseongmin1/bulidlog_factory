"""대시보드와 에이전트 API 서버. 빌드된 React 화면(frontend/dist)도 함께 제공한다."""
import json
import os
import uuid
from collections import OrderedDict

import openai
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import analysis, dayahead, responses
from .agent import run_turn
from . import forecast as alarm_forecast
from .briefing import daily_briefing
from .db import ROOT, DataUnavailableError

DIST_DIR = ROOT / "frontend" / "dist"
MAX_SESSIONS = 100

app = FastAPI(title="Factory Energy Agent")
# 화면을 아직 빌드하지 않았어도 서버가 켜지도록 폴더 존재 확인은 요청이 올 때 한다.
app.mount("/assets", StaticFiles(directory=DIST_DIR / "assets", check_dir=False), name="assets")
sessions: OrderedDict[str, list] = OrderedDict()


class AlarmResponse(BaseModel):
    time: str
    target: str
    level: str
    probability_pct: int
    response: str


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    session_id: str | None = None


@app.exception_handler(DataUnavailableError)
async def data_unavailable(_: Request, exc: DataUnavailableError) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": str(exc)})


def has_credentials() -> bool:
    return bool(os.getenv("OPENAI_API_KEY", "").strip())


def get_history(session_id: str) -> list:
    history = sessions.setdefault(session_id, [])
    sessions.move_to_end(session_id)
    while len(sessions) > MAX_SESSIONS:
        sessions.popitem(last=False)
    return history


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def _api_error_message(exc: openai.OpenAIError) -> str:
    if isinstance(exc, openai.AuthenticationError):
        return "API 키가 유효하지 않습니다. backend/.env 파일의 OPENAI_API_KEY를 확인해 주세요."
    if isinstance(exc, openai.NotFoundError):
        return "모델을 찾을 수 없습니다. backend/.env의 OPENAI_MODEL 이름과 계정에서 쓸 수 있는 모델인지 확인해 주세요."
    if isinstance(exc, openai.RateLimitError):
        return "요청이 많거나 크레딧이 부족합니다. 잠시 후 다시 시도하거나 OpenAI 결제·한도를 확인해 주세요."
    if isinstance(exc, openai.APIConnectionError):
        return "OpenAI API에 연결하지 못했습니다. 네트워크를 확인해 주세요."
    if isinstance(exc, openai.APIStatusError):
        return f"OpenAI API 오류가 발생했습니다 (HTTP {exc.status_code})."
    return "OpenAI API 호출 중 오류가 발생했습니다."


@app.get("/")
def index():
    page = DIST_DIR / "index.html"
    if page.is_file():
        return FileResponse(page)
    return HTMLResponse(
        "<p>화면이 아직 만들어지지 않았습니다. <code>frontend</code> 폴더에서 <code>npm run build</code>를 실행하세요.</p>",
        status_code=503,
    )


@app.get("/api/status")
def status() -> dict:
    return {"agent_ready": has_credentials()}


@app.get("/api/dashboard")
def dashboard() -> dict:
    return analysis.dashboard()


@app.get("/api/anomalies")
def anomalies() -> dict:
    return {"items": analysis.top_anomalies()}


@app.get("/api/report/{date}")
def report(date: str) -> dict:
    try:
        data = analysis.daily_report(date)
    except DataUnavailableError:
        raise
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {**data, "markdown": analysis.report_markdown(data)}


@app.get("/api/briefing/{date}")
def briefing(date: str, ai: bool = True) -> dict:
    """그날의 자동 요약. ai=false면 코드 요약만 바로 준다. 키가 없거나 AI가 실패해도 코드 요약을 돌려준다."""
    try:
        return daily_briefing(date, openai.OpenAI() if ai and has_credentials() else None)
    except DataUnavailableError:
        raise
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/power-factor-plan")
def power_factor_plan(start: str, end: str, target: float) -> dict:
    """09~22시 역률을 목표까지 올리는 콘덴서 용량 계산. 자동 조정기와 고정 콘덴서를 비교한다."""
    try:
        return analysis.plan_power_factor_correction(start, end, target)
    except DataUnavailableError:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/forecast/day/{date}")
def forecast_day(date: str) -> dict:
    """하루 96칸의 경보와 실제 값, 그날 경보가 몇 분 먼저 켜졌는지"""
    try:
        return alarm_forecast.day_timeline(date)
    except DataUnavailableError:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/forecast")
def forecast(time: str) -> dict:
    """time(YYYY-MM-DD HH:MM)을 '지금'으로 본 1시간 경보와, 같은 모델의 9~12월 채점 결과. AI는 쓰지 않는다."""
    try:
        return {**alarm_forecast.forecast_at(time), "scores": alarm_forecast.scores()}
    except DataUnavailableError:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/day-ahead/{date}")
def day_ahead(date: str) -> dict:
    """하루 전까지의 데이터로 본 그 날의 위험 시간대와 계획, 같은 방식의 9~12월 채점 결과. AI는 쓰지 않는다."""
    try:
        return {**dayahead.day_ahead(date), "scores": dayahead.scores()}
    except DataUnavailableError:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/alarm-responses")
def save_alarm_response(body: AlarmResponse) -> dict:
    """경보에 대한 작업자의 응답(조치함·무시함·오경보)을 이 컴퓨터의 파일에 기록한다."""
    try:
        return responses.record(body.time, body.target, body.level, body.probability_pct, body.response)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/alarm-responses/day/{date}")
def alarm_responses_day(date: str) -> dict:
    return responses.lookup(date)


@app.get("/api/alarm-responses/stats")
def alarm_response_stats() -> dict:
    """쌓인 대응 기록의 통계와, 문턱을 올렸을 때의 변화"""
    return responses.stats()


@app.post("/api/chat")
def chat(request: ChatRequest) -> StreamingResponse:
    session_id = request.session_id or uuid.uuid4().hex
    history = get_history(session_id)

    def events():
        yield _sse({"type": "session", "session_id": session_id})
        if not has_credentials():
            yield _sse({"type": "error", "message": "OPENAI_API_KEY가 없습니다. backend/.env 파일에 키를 넣고 서버를 다시 시작해 주세요."})
            return
        try:
            for event in run_turn(openai.OpenAI(), history, request.message):
                yield _sse(event)
        except openai.OpenAIError as exc:
            yield _sse({"type": "error", "message": _api_error_message(exc)})

    return StreamingResponse(events(), media_type="text/event-stream")
