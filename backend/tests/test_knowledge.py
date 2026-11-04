"""현장 매뉴얼 검색 품질을 질문 세트로 고정한다. 문서나 검색 방식을 바꾸면 이 테스트로 나빠졌는지 확인한다."""
import pytest

from app import knowledge

# (질문, 맞는 문서들). 상위 3개 절 안에 맞는 문서가 있어야 한다.
LABELED = [
    ("역률 경보 뜨면 뭐 해야 돼?", {"SOP-EN-02"}),
    ("피크 넘으면 어떻게 해", {"SOP-EN-01", "SOP-EN-04"}),
    ("수요전력 위험 경보 조치", {"SOP-EN-01"}),
    ("콘덴서 너무 많이 켜면?", {"SOP-EN-03", "GUIDE-01"}),
    ("교대 시간이 어떻게 돼?", {"SOP-OP-01"}),
    ("인수인계서에 뭘 써야 해?", {"SOP-OP-01"}),
    ("일요일에 특근하면 신고해야 해?", {"SOP-OP-03"}),
    ("kVarh가 뭐야", {"GUIDE-01"}),
    ("오경보 버튼은 언제 눌러?", {"SOP-OP-02"}),
    ("설비 켜는 순서", {"SOP-EN-04"}),
    ("콘덴서에서 냄새가 나요", {"SOP-EN-02"}),
    ("응답 안 한 경보는 어떻게 돼?", {"SOP-OP-02"}),
    ("역률이 뭐야", {"GUIDE-01", "SOP-EN-02"}),
]
UNRELATED = ["점심 메뉴 추천", "2019년 생산량", "오늘 날씨 어때", "주식 추천해줘"]


@pytest.mark.parametrize("query, expected", LABELED)
def test_relevant_document_is_in_top_three(query, expected):
    found = {r["doc_id"] for r in knowledge.search(query, 3)["results"]}
    assert found & expected, f"{query}: {found}"


def test_top_one_document_accuracy_stays_high():
    hits = sum(knowledge.search(q, 1)["results"][0]["doc_id"] in docs for q, docs in LABELED)
    assert hits >= 12


@pytest.mark.parametrize("query", UNRELATED)
def test_unrelated_questions_find_nothing(query):
    result = knowledge.search(query)
    assert result["results"] == [] and result["찾지_못함"]


def test_alarm_cards_get_the_matching_procedure():
    assert knowledge.for_alarm("usage")[0]["cite"] == "[SOP-EN-01 2. 경보 단계별 조치]"
    assert knowledge.for_alarm("power_factor")[0]["cite"] == "[SOP-EN-02 2. 경보가 켜지면]"


def test_documents_are_marked_as_examples_and_can_be_opened():
    docs = knowledge.documents()
    assert len(docs) >= 8 and all("예시 문서" in d["meta"] for d in docs)
    doc = knowledge.document("SOP-OP-01")
    assert doc["title"] == "교대 인수인계 작성 기준" and doc["sections"][0]["heading"] == "1. 교대 시간"
    with pytest.raises(KeyError):
        knowledge.document("없는-문서")


def test_bad_input_is_rejected():
    with pytest.raises(ValueError):
        knowledge.search("  ")
    with pytest.raises(ValueError):
        knowledge.search("역률", top_k=9)
