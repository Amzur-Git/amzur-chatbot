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

_TABLE_REQUEST_PATTERN = re.compile(r"\b(table|tabular|markdown table|in a table format|as a table)\b", re.IGNORECASE)
_SCIENTIFIC_NOTATION_PATTERN = re.compile(r"(?<![A-Za-z0-9_])[-+]?\d+(?:\.\d+)?[eE][+-]?\d+(?![A-Za-z0-9_])")


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


def _is_table_request(question: str) -> bool:
    return bool(_TABLE_REQUEST_PATTERN.search(question or ""))


def _format_numeric_token(value: float) -> str:
    if float(value).is_integer():
        return f"{int(round(value)):,}"

    abs_value = abs(value)
    if abs_value >= 1:
        return f"{value:,.6f}".rstrip("0").rstrip(".")

    return f"{value:.10f}".rstrip("0").rstrip(".")


def _normalize_scientific_notation(answer: str) -> str:
    text = (answer or "").strip()
    if not text:
        return text

    def _replace(match: re.Match[str]) -> str:
        token = match.group(0)
        try:
            parsed = float(token)
        except ValueError:
            return token
        return _format_numeric_token(parsed)

    return _SCIENTIFIC_NOTATION_PATTERN.sub(_replace, text)


def _extract_best_observation(steps: list[dict[str, Any]]) -> str:
    """Fallback: return the last non-empty tool observation when model output is empty."""
    for step in reversed(steps or []):
        observation = str(step.get("observation", "")).strip()
        if observation:
            return observation
    return ""


def _steps_contain_execution_error(steps: list[dict[str, Any]]) -> bool:
    error_markers = (
        "syntaxerror",
        "traceback",
        "nameerror",
        "valueerror",
        "typeerror",
        "exception",
        "unterminated string literal",
    )
    for step in steps or []:
        obs = str(step.get("observation", "")).lower()
        if any(marker in obs for marker in error_markers):
            return True
    return False


def _build_execution_forcing_question(question: str, *, prefer_table: bool = False) -> str:
    normalized = (question or "").lower()
    spend_hint = ""
    if "spent" in normalized or "expense" in normalized:
        spend_hint = (
            " For spending/expense questions, report spend as positive magnitude values "
            "(use absolute values after computation if source values are negative accounting entries)."
        )

    format_hint = ""
    if prefer_table:
        format_hint = (
            " Format the final output as a markdown table with clear headers. "
            "Return only the table and a one-line summary if needed. "
            "Use standard decimal notation for numbers (no scientific notation)."
        )

    return (
        "Use Python on the provided pandas DataFrame `df` and compute the final result now. "
        "Do not describe your plan or steps. Return only the final answer with computed values. "
        f"If grouped totals are requested, include each group and its numeric total.{spend_hint}{format_hint}\n\n"
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

    wants_table = _is_table_request(normalized_question)
    forced_question = _build_execution_forcing_question(normalized_question, prefer_table=wants_table)

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

    if _looks_like_plan_only_answer(answer) or not answer:
        retry_question = (
            "Your previous response was not a final computed answer. "
            "Execute the computation on `df` and return only the final result values now. "
            "If no rows match, explicitly say that no matching data was found.\n\n"
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

    # Additional fallback specifically for table-style asks, which can be sensitive to formatting instructions.
    if wants_table and not answer:
        table_retry_question = _build_execution_forcing_question(
            normalized_question,
            prefer_table=True,
        ) + (
            "\n\nIf no records match, respond exactly with: No matching data found for your query in the current sheet."
        )
        try:
            table_retry_result = agent.invoke({"input": table_retry_question})
        except Exception:
            table_retry_result = None

        if isinstance(table_retry_result, dict):
            table_retry_answer = str(table_retry_result.get("output", "")).strip()
            table_retry_steps = _serialize_intermediate_steps(table_retry_result.get("intermediate_steps"))
            if table_retry_answer:
                answer = table_retry_answer
                steps = table_retry_steps
        elif table_retry_result is not None:
            table_retry_answer = str(table_retry_result).strip()
            if table_retry_answer:
                answer = table_retry_answer

    # Recovery path: table-style tool code can fail due formatting/syntax generation.
    # Retry once with simpler instructions and prefer a stable markdown conversion path.
    had_execution_error = _steps_contain_execution_error(steps)
    if wants_table and not answer and had_execution_error:
        recovery_question = (
            "Retry with robust execution. Avoid manual multiline string building. "
            "Compute the result in a pandas DataFrame and use DataFrame.to_markdown(index=False) for table output. "
            "If no rows match, respond exactly with: No matching data found for your query in the current sheet.\n\n"
            f"Question: {normalized_question}"
        )
        try:
            recovery_result = agent.invoke({"input": recovery_question})
        except Exception:
            recovery_result = None

        if isinstance(recovery_result, dict):
            recovery_answer = str(recovery_result.get("output", "")).strip()
            recovery_steps = _serialize_intermediate_steps(recovery_result.get("intermediate_steps"))
            if recovery_answer:
                answer = recovery_answer
                steps = recovery_steps
        elif recovery_result is not None:
            recovery_answer = str(recovery_result).strip()
            if recovery_answer:
                answer = recovery_answer

    if not answer:
        observed = _extract_best_observation(steps)
        if _steps_contain_execution_error(steps):
            answer = (
                "I could not complete this query due to an internal formatting/execution issue while processing the table request. "
                "Please retry once, or remove the table-format instruction and I will return the computed result."
            )
        else:
            answer = observed or "No matching data found for your query in the current sheet."

    answer = _normalize_scientific_notation(answer)

    return {
        "success": True,
        "answer": answer,
        "intermediate_steps": steps,
    }
