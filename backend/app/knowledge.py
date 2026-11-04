"""현장 매뉴얼 검색(RAG의 검색 단계). backend/knowledge/*.md를 절(## ) 단위로 나눠 색인하고, 질문과 가까운 절을 찾는다.

- 형태소 분석기 없이 한국어를 다루려고 두 가지 TF-IDF를 섞는다. 조사를 뗀 낱말(역률이→역률)과 글자 2~3개 묶음(char n-gram)이다.
- 질문의 채움말(뭐, 어떻게, 해야 돼)은 지우고, 현장 말(피크)은 문서 말(수요전력)로 넓힌다.
- 절 점수에 문서 전체 점수를 30% 섞어, 짧은 절 하나의 우연한 일치보다 주제가 맞는 문서를 앞에 둔다.
- 문턱(MIN_SCORE)은 tests/test_knowledge.py의 질문 세트에서 관련 질문과 관련 없는 질문(점심 메뉴, 날씨)이 갈리도록 정했다.
- 인터넷과 API 키 없이 동작하고, 같은 질문에는 늘 같은 결과를 낸다. 그래서 자동 테스트로 검색 품질을 고정할 수 있다.
- 찾은 절은 AI 에이전트의 search_manual 도구, 경보 카드의 '조치 매뉴얼', 교대 인수인계서의 참고 문서로 쓴다.
"""
import re
from functools import lru_cache
from pathlib import Path

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import linear_kernel

DIR = Path(__file__).resolve().parents[1] / "knowledge"
MIN_SCORE = 0.14  # 이보다 낮으면 관련 문서가 없다고 본다.
DOC_WEIGHT = 0.3
PARTICLE = re.compile(r"(에서|으로|하면|하나요|해요|은|는|이|가|을|를|에|로|의|도|만|요)$")
FILLER = re.compile(r"(뭐야|뭐|어떻게|해야|돼|해요|해줘|알려줘|언제|있어|하면|되나요|나요|무엇|어때|좀)")
SYNONYMS = {"피크": "수요전력 최대", "효율": "역률", "커패시터": "콘덴서", "근무조": "교대", "인계": "인수인계", "뜨면": "켜지면", "울리면": "켜지면"}
MAX_TOP_K = 5
NOTICE = "예시 문서입니다. 포트폴리오 시연용으로 작성했으며 실제 공장의 규정이 아닙니다."

# 경보 카드와 인수인계서에서 쓰는 고정 질문. 결과가 늘 같아야 하므로 AI가 아니라 검색만 쓴다.
ALARM_QUERIES = {
    "usage": "수요전력 초과 경보 단계별 조치",
    "power_factor": "역률 경보가 켜지면 조정기 표시창 콘덴서 단계 확인",
}


def _parse(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    title = re.search(r"^# (.+)$", text, re.M).group(1).strip()
    meta = re.search(r"^> (.+)$", text, re.M)
    doc_id = path.stem
    sections = []
    for match in re.finditer(r"^## (.+?)\n(.*?)(?=^## |\Z)", text, re.M | re.S):
        heading, body = match.group(1).strip(), match.group(2).strip()
        sections.append({"heading": heading, "text": body})
    return {"doc_id": doc_id, "title": title, "meta": meta.group(1).strip() if meta else "", "sections": sections, "text": text}


def _words(text: str) -> list[str]:
    return [PARTICLE.sub("", w) or w for w in re.findall(r"[가-힣A-Za-z0-9]+", text)]


def _vectorizers() -> list[TfidfVectorizer]:
    return [
        TfidfVectorizer(analyzer=_words, sublinear_tf=True),
        TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 3), sublinear_tf=True),
    ]


def _prepare_query(query: str) -> str:
    expanded = query + " " + " ".join(v for k, v in SYNONYMS.items() if k in query)
    return FILLER.sub(" ", expanded)


def _scores(pairs: list, query: str):
    return sum(linear_kernel(v.transform([query]), m).ravel() for v, m in pairs) / len(pairs)


@lru_cache(maxsize=1)
def _index() -> dict:
    docs = [_parse(p) for p in sorted(DIR.glob("*.md"))]
    if not docs:
        raise ValueError(f"매뉴얼 문서가 없습니다: {DIR}")
    chunks = [
        {"doc_id": d["doc_id"], "title": d["title"], "section": s["heading"], "text": s["text"]}
        for d in docs
        for s in d["sections"]
    ]
    # 제목은 절마다 붙여 넣어, 제목에만 있는 낱말(예: '진상')로도 찾을 수 있게 한다.
    texts = [f"{c['title']} {c['section']} {c['text']}" for c in chunks]
    sections = [(v, v.fit_transform(texts)) for v in _vectorizers()]
    whole = [(v, v.fit_transform([d["text"] for d in docs])) for v in _vectorizers()]
    doc_row = {d["doc_id"]: i for i, d in enumerate(docs)}
    return {"docs": docs, "chunks": chunks, "sections": sections, "whole": whole, "doc_of": [doc_row[c["doc_id"]] for c in chunks]}


def search(query: str, top_k: int = 3) -> dict:
    query = (query or "").strip()
    if not query:
        raise ValueError("검색어를 입력하세요.")
    if not 1 <= int(top_k) <= MAX_TOP_K:
        raise ValueError(f"top_k는 1~{MAX_TOP_K} 사이여야 합니다.")
    index = _index()
    prepared = _prepare_query(query)
    doc_scores = _scores(index["whole"], prepared)
    scores = (1 - DOC_WEIGHT) * _scores(index["sections"], prepared) + DOC_WEIGHT * doc_scores[index["doc_of"]]
    order = scores.argsort()[::-1][: int(top_k)]
    results = [
        {**index["chunks"][i], "score": round(float(scores[i]), 3), "cite": f"[{index['chunks'][i]['doc_id']} {index['chunks'][i]['section']}]"}
        for i in order
        if scores[i] >= MIN_SCORE
    ]
    return {
        "query": query,
        "results": results,
        "찾지_못함": not results,
        "주의": NOTICE + " 답변에는 cite 표기로 출처를 밝히고, 결과에 없는 절차는 지어내지 않는다.",
    }


def for_alarm(target: str, top_k: int = 2) -> list[dict]:
    if target not in ALARM_QUERIES:
        raise ValueError(f"알 수 없는 경보 대상입니다: {target}")
    return search(ALARM_QUERIES[target], top_k)["results"]


def documents() -> list[dict]:
    return [
        {"doc_id": d["doc_id"], "title": d["title"], "meta": d["meta"], "sections": [s["heading"] for s in d["sections"]]}
        for d in _index()["docs"]
    ]


def document(doc_id: str) -> dict:
    for d in _index()["docs"]:
        if d["doc_id"] == doc_id:
            return {"doc_id": d["doc_id"], "title": d["title"], "meta": d["meta"], "sections": d["sections"]}
    raise KeyError(f"문서를 찾을 수 없습니다: {doc_id}")
