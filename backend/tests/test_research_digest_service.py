from app.services.research_digest_service import PaperRecord, ResearchDigestService


def test_parse_json_decision_from_fenced_block():
    payload = """```json
    {"enough": true, "reason": "coverage is sufficient", "confidence": 0.81, "missing_aspects": []}
    ```"""

    parsed = ResearchDigestService._parse_json_decision(payload)

    assert parsed is not None
    assert parsed["enough"] is True
    assert parsed["confidence"] == 0.81


def test_parse_json_decision_from_embedded_object():
    payload = "Model says: {\"enough\": false, \"reason\": \"need more methods papers\", \"confidence\": 0.4, \"missing_aspects\": [\"benchmarks\"]}"

    parsed = ResearchDigestService._parse_json_decision(payload)

    assert parsed is not None
    assert parsed["enough"] is False
    assert parsed["missing_aspects"] == ["benchmarks"]


def test_build_fallback_digest_contains_topic_and_references():
    paper = [
        PaperRecord(
            title="Attention Is All You Need",
            summary="Introduces a self-attention architecture for sequence transduction.",
            authors="Ashish Vaswani, Noam Shazeer",
            url="https://arxiv.org/abs/1706.03762",
            published="2017-06-12",
            entry_id="1706.03762",
        )
    ]

    digest = ResearchDigestService._build_fallback_digest(topic="transformer attention", papers=paper)

    assert "Executive Summary" in digest
    assert "transformer attention" in digest
    assert "References" in digest
