from app import grounding


def test_rounded_numbers_match_at_the_answer_precision():
    tool = '{"usage_kwh": 2014.66, "기준선_대비_pct": 193.4, "date": "2018-12-16"}'
    assert grounding.check("2,015kWh를 썼어요 (12월 16일)", tool)["ok"]
    assert grounding.check("2,014.7kWh", tool)["ok"]
    result = grounding.check("평소보다 194% 많아요", tool)
    assert result["ungrounded"] == ["194"]


def test_unit_conversion_and_small_counts_are_allowed():
    tool = {"usage_kwh": 959636.71}
    assert grounding.check("1년에 959.6MWh를 썼고, 3가지 이유가 있어요.", tool)["ok"]
    assert grounding.check("1. 첫째\n2. 둘째", tool)["checked"] == 0


def test_strict_mode_requires_the_exact_number():
    facts = {"요약_문장": ["전기를 2,015kWh 썼어요."]}
    assert grounding.check("2,015kWh예요", facts, strict=True)["ok"]
    assert not grounding.check("2,014kWh예요", facts, strict=True)["ok"]
    assert not grounding.check("3번 경보", facts, strict=True)["ok"]


def test_sources_include_nested_values_and_question_text():
    found = grounding.source_numbers({"a": [1.5, {"b": "13:45"}]}, "12월 16일")
    assert {1.5, 13, 45, 12, 16} <= found
