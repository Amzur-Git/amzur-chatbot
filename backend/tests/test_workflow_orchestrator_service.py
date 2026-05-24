from app.schemas.workflow import WorkflowRunRequest
from app.services.workflow_orchestrator_service import WorkflowOrchestratorService


def test_pick_topic_prefers_explicit_topic():
    payload = WorkflowRunRequest(
        chat_thread_id="00000000-0000-0000-0000-000000000001",
        user_request="Summarize current trends in retrieval augmented generation.",
        topic="RAG evaluation methods",
    )

    topic = WorkflowOrchestratorService._pick_topic(payload)

    assert topic == "RAG evaluation methods"


def test_pick_topic_falls_back_to_user_request():
    payload = WorkflowRunRequest(
        chat_thread_id="00000000-0000-0000-0000-000000000001",
        user_request="How are multimodal agents evaluated in production?",
        topic=None,
    )

    topic = WorkflowOrchestratorService._pick_topic(payload)

    assert topic == "How are multimodal agents evaluated in production?"


def test_parse_sse_event_extracts_event_and_json_data():
    raw = 'event: final_digest\ndata: {"digest": "hello"}\n\n'

    parsed = WorkflowOrchestratorService._parse_sse_event(raw)

    assert parsed is not None
    event_name, payload = parsed
    assert event_name == "final_digest"
    assert payload["digest"] == "hello"
