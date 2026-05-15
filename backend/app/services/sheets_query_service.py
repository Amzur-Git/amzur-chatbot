from __future__ import annotations

import re
from typing import Any

import pandas as pd
from fastapi import HTTPException

from app.ai.llm import llm

try:
    from langchain.agents.agent_types import AgentType
except Exception:  # pragma: no cover - version dependent import
    AgentType = None

try:
    from langchain_experimental.agents import create_pandas_dataframe_agent
except ImportError:  # pragma: no cover - environment dependent
    create_pandas_dataframe_agent = None


def _resolve_agent_type() -> Any:
    if AgentType is None:
        return "openai-functions"

    if hasattr(AgentType, "OPENAI_FUNCTIONS"):
        return AgentType.OPENAI_FUNCTIONS
    if hasattr(AgentType, "ZERO_SHOT_REACT_DESCRIPTION"):
        return AgentType.ZERO_SHOT_REACT_DESCRIPTION
    return "openai-functions"


def _serialize_intermediate_steps(intermediate_steps: Any) -> list[dict[str, Any]]:
    if not intermediate_steps:
        return []

    serialized: list[dict[str, Any]] = []
    for item in intermediate_steps:
        if isinstance(item, tuple) and len(item) == 2:
            action, observation = item
            serialized.append(
                {
                    "tool": getattr(action, "tool", None),
                    "tool_input": getattr(action, "tool_input", None),
                    "log": getattr(action, "log", None),
                    "observation": str(observation),
                }
            )
        else:
            serialized.append({"detail": str(item)})

    return serialized


_PLAN_ONLY_PATTERN = re.compile(
    r"\b(i need to|let'?s start by|the following steps|first,|next,|then,)\b",
    re.IGNORECASE,
)


def _looks_like_plan_only_answer(answer: str) -> bool:
    """Detect responses that describe intended steps instead of computed results."""
    text = (answer or "").strip()
    if not text:
        return False

    if _PLAN_ONLY_PATTERN.search(text):
        return True

    # If there are no numbers/totals/tables and the tone is procedural, treat as non-final.
    if any(keyword in text.lower() for keyword in ["filter", "group", "convert", "calculate"]) and not re.search(r"\d", text):
        return True

    return False


def _build_execution_forcing_question(question: str) -> str:
    normalized = (question or "").lower()
    spend_hint = ""
    if "spent" in normalized or "expense" in normalized:
        spend_hint = (
            " For spending/expense questions, report spend as positive magnitude values "
            "(use absolute values after computation if source values are negative accounting entries)."
        )

    return (
        "Use Python on the provided pandas DataFrame `df` and compute the final result now. "
        "Do not describe your plan or steps. Return only the final answer with computed values. "
        f"If grouped totals are requested, include each group and its numeric total.{spend_hint}\n\n"
        f"Question: {question}"
    )


def query_dataframe_with_langchain(df: pd.DataFrame, question: str) -> dict[str, Any]:
    if create_pandas_dataframe_agent is None:
        raise HTTPException(
            status_code=500,
            detail=(
                "langchain_experimental is required for DataFrame querying. "
                "Install langchain-experimental in the backend environment."
            ),
        )

    if df.empty:
        raise HTTPException(status_code=400, detail="DataFrame has no rows to analyze.")

    normalized_question = (question or "").strip()
    if not normalized_question:
        raise HTTPException(status_code=400, detail="Question cannot be empty.")

    try:
        agent = create_pandas_dataframe_agent(
            llm=llm,
            df=df,
            agent_type=_resolve_agent_type(),
            max_iterations=10,
            # Required for pandas-agent code execution in controlled backend workloads.
            allow_dangerous_code=True,
            return_intermediate_steps=True,
            verbose=True,
        )
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"Failed to initialize DataFrame agent: {error}") from error

    forced_question = _build_execution_forcing_question(normalized_question)

    try:
        result = agent.invoke({"input": forced_question})
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"DataFrame query failed: {error}") from error

    if isinstance(result, dict):
        answer = str(result.get("output", "")).strip()
        steps = _serialize_intermediate_steps(result.get("intermediate_steps"))
    else:
        answer = str(result).strip()
        steps = []

    if _looks_like_plan_only_answer(answer):
        retry_question = (
            "Your previous response was not a final computed answer. "
            "Execute the computation on `df` and return only the final result values now.\n\n"
            f"Question: {normalized_question}"
        )
        try:
            retry_result = agent.invoke({"input": retry_question})
        except Exception:
            retry_result = None

        if isinstance(retry_result, dict):
            retry_answer = str(retry_result.get("output", "")).strip()
            retry_steps = _serialize_intermediate_steps(retry_result.get("intermediate_steps"))
            if retry_answer and not _looks_like_plan_only_answer(retry_answer):
                answer = retry_answer
                steps = retry_steps
        elif retry_result is not None:
            retry_answer = str(retry_result).strip()
            if retry_answer and not _looks_like_plan_only_answer(retry_answer):
                answer = retry_answer

    if not answer:
        raise HTTPException(status_code=500, detail="DataFrame agent returned an empty response.")

    return {
        "success": True,
        "answer": answer,
        "intermediate_steps": steps,
    }
