"""AI 에이전트 자동 평가. 질문 세트(backend/evals/cases.json)를 실제 모델에 보내고 기계적으로 채점한다.

    python -m app.evals                 # 전체
    python -m app.evals --only money,control

채점 항목 (모두 사람이 아닌 코드가 판정한다)
- 도구 선택: expect_tools 중 하나 이상을 불렀는가. 비어 있으면 아무 도구나 괜찮다. max_tools가 있으면 그 이하로만 불러야 한다.
- 숫자 근거: 답변의 숫자가 모두 도구 결과·질문에 있는가 (grounding.check, 대화 화면의 배지와 같은 검사).
- 규칙: must_not 정규식이 답변에 없고, must_include_any 중 하나가 답변에 있는가.
결과는 backend/local/evals/에 JSON과 마크다운 표로 남긴다. 실제 API를 부르므로 요금이 나온다.
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import openai

from .agent import run_turn
from .llm import model_name

CASES = Path(__file__).resolve().parents[1] / "evals" / "cases.json"
OUT = Path(__file__).resolve().parents[1] / "local" / "evals"


def load_cases(only: list[str] | None = None) -> list[dict]:
    cases = json.loads(CASES.read_text(encoding="utf-8"))
    if only:
        unknown = set(only) - {c["id"] for c in cases}
        if unknown:
            raise ValueError(f"없는 평가 항목입니다: {', '.join(sorted(unknown))}")
        cases = [c for c in cases if c["id"] in only]
    return cases


def grade(case: dict, events: list[dict], seconds: float) -> dict:
    tools = [e["name"] for e in events if e["type"] == "tool_call"]
    answer = next((e["text"] for e in events if e["type"] == "answer"), "")
    verification = next((e for e in events if e["type"] == "verification"), None)
    error = next((e["message"] for e in events if e["type"] == "error"), None)

    expected = case.get("expect_tools", [])
    tool_ok = (not expected or bool(set(tools) & set(expected))) and len(tools) <= case.get("max_tools", 99)
    grounded_ok = verification is not None and verification["ok"]
    broken = [p for p in case.get("must_not", []) if re.search(p, answer)]
    include = case.get("must_include_any", [])
    include_ok = not include or any(re.search(p, answer) for p in include)
    rule_ok = not broken and include_ok
    return {
        "id": case["id"],
        "category": case.get("category", ""),
        "question": case["question"],
        "passed": error is None and tool_ok and grounded_ok and rule_ok,
        "tool_ok": tool_ok,
        "grounded_ok": grounded_ok,
        "rule_ok": rule_ok,
        "tools": tools,
        "numbers_checked": verification["checked"] if verification else 0,
        "ungrounded": verification["ungrounded"] if verification else [],
        "broken_rules": broken,
        "missing_phrase": None if include_ok else include,
        "seconds": round(seconds, 1),
        "error": error,
        "answer": answer,
    }


def run_case(client: openai.OpenAI, case: dict) -> dict:
    started = time.perf_counter()
    try:
        events = list(run_turn(client, [], case["question"]))
    except openai.OpenAIError as exc:
        events = [{"type": "error", "message": f"{type(exc).__name__}"}]
    return grade(case, events, time.perf_counter() - started)


def summarize(results: list[dict]) -> dict:
    n = len(results)
    rate = lambda key: round(sum(r[key] for r in results) / n * 100, 1) if n else None  # noqa: E731
    checked = sum(r["numbers_checked"] for r in results)
    missing = sum(len(r["ungrounded"]) for r in results)
    return {
        "model": model_name(),
        "cases": n,
        "통과율_pct": rate("passed"),
        "도구_선택_정확도_pct": rate("tool_ok"),
        "숫자_근거_통과율_pct": rate("grounded_ok"),
        "규칙_준수율_pct": rate("rule_ok"),
        "검사한_숫자": checked,
        "근거_없는_숫자": missing,
        "평균_응답_초": round(sum(r["seconds"] for r in results) / n, 1) if n else None,
    }


def report_markdown(summary: dict, results: list[dict], when: str) -> str:
    mark = lambda ok: "✅" if ok else "❌"  # noqa: E731
    lines = [
        f"# 에이전트 평가 결과 ({when}, 모델 {summary['model']})",
        "",
        f"- 통과 {summary['통과율_pct']}% ({sum(r['passed'] for r in results)}/{summary['cases']})",
        f"- 도구 선택 정확도 {summary['도구_선택_정확도_pct']}% · 숫자 근거 통과 {summary['숫자_근거_통과율_pct']}% · 규칙 준수 {summary['규칙_준수율_pct']}%",
        f"- 답변 속 숫자 {summary['검사한_숫자']}개 중 도구 결과에서 찾지 못한 숫자 {summary['근거_없는_숫자']}개 · 평균 {summary['평균_응답_초']}초",
        "",
        "| 항목 | 분류 | 통과 | 도구 | 숫자 근거 | 규칙 | 부른 도구 | 메모 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        memo = []
        if r["ungrounded"]:
            memo.append("근거 없는 숫자: " + ", ".join(r["ungrounded"]))
        if r["broken_rules"]:
            memo.append("금지 표현")
        if r["missing_phrase"]:
            memo.append("필요한 표현 없음")
        if r["error"]:
            memo.append(f"오류: {r['error']}")
        lines.append(
            f"| {r['id']} | {r['category']} | {mark(r['passed'])} | {mark(r['tool_ok'])} | {mark(r['grounded_ok'])} | "
            f"{mark(r['rule_ok'])} | {', '.join(r['tools']) or '-'} | {'; '.join(memo) or '-'} |"
        )
    lines += ["", "채점은 코드가 합니다. 답변이 '좋은지'는 판단하지 않고, 정한 규칙을 지켰는지만 봅니다."]
    return "\n".join(lines)


def run(client: openai.OpenAI, cases: list[dict], out_dir: Path = OUT) -> tuple[dict, Path]:
    results = []
    for case in cases:
        result = run_case(client, case)
        results.append(result)
        print(f"{'통과' if result['passed'] else '실패'} {case['id']} ({result['seconds']}초) 도구: {', '.join(result['tools']) or '-'}")
    summary = summarize(results)
    when = datetime.now().strftime("%Y-%m-%d %H:%M")
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    (out_dir / f"eval-{stamp}.json").write_text(
        json.dumps({"when": when, "summary": summary, "results": results}, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    path = out_dir / f"eval-{stamp}.md"
    path.write_text(report_markdown(summary, results, when) + "\n", encoding="utf-8")
    return summary, path


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="AI 에이전트를 질문 세트로 자동 채점합니다. 실제 API 요금이 나옵니다.")
    parser.add_argument("--only", help="쉼표로 구분한 항목 id만 실행")
    args = parser.parse_args(argv)
    if not os.getenv("OPENAI_API_KEY", "").strip():
        raise SystemExit("OPENAI_API_KEY가 없습니다. backend/.env에 키를 넣으세요.")
    try:
        cases = load_cases(args.only.split(",") if args.only else None)
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    summary, path = run(openai.OpenAI(), cases)
    print(f"\n통과 {summary['통과율_pct']}% · 도구 {summary['도구_선택_정확도_pct']}% · 숫자 근거 {summary['숫자_근거_통과율_pct']}% · 규칙 {summary['규칙_준수율_pct']}%")
    print(f"결과: {path}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
