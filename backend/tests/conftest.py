"""앱을 불러오기 전에 임시 SQLite 데이터베이스를 만들어 연결한다.

실제 database/energy.db나 Supabase를 건드리지 않고, 테스트가 끝나도 남지 않는 임시 폴더만 쓴다.
"""
import os
import tempfile
from pathlib import Path

_test_db = Path(tempfile.mkdtemp(prefix="energy-test-")) / "test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_test_db.as_posix()}"

from app import loader  # noqa: E402

loader.load()


import json  # noqa: E402
import threading  # noqa: E402
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer  # noqa: E402

import openai  # noqa: E402
import pytest  # noqa: E402


def completion(content=None, tool_calls=None, finish_reason="stop"):
    """가짜 Chat Completions 응답 하나"""
    message = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = [
            {"id": f"call_{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}
            for i, (name, args) in enumerate(tool_calls)
        ]
        finish_reason = "tool_calls"
    return {
        "id": "chatcmpl-test", "object": "chat.completion", "created": 0, "model": "test",
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


@pytest.fixture
def fake_openai(monkeypatch):
    """실제 SDK가 붙는 가짜 OpenAI 서버. state["script"]의 응답을 차례로 돌려준다(마지막 것은 반복)."""
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    state = {"script": [completion("안녕하세요")], "requests": []}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            state["requests"].append(json.loads(self.rfile.read(int(self.headers["content-length"]))))
            reply = state["script"].pop(0) if len(state["script"]) > 1 else state["script"][0]
            payload = json.dumps(reply).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    state["client"] = openai.OpenAI(api_key="test", base_url=f"http://127.0.0.1:{server.server_port}/v1", max_retries=0)
    yield state
    server.shutdown()
