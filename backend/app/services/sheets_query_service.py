from __future__ import annotations

import re
from difflib import get_close_matches
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
_GROUP_BY_PATTERN = re.compile(
    r"\b(?:split|group)\s+(?:the\s+data\s+)?(?:based\s+on|by)\s+([a-zA-Z0-9_\-/& ]+)",
    re.IGNORECASE,
)
_FINANCE_TOTAL_PATTERN = re.compile(
    r"\btotal\s+(?:spent|expense|cost)\s+on\s+(?P<account>.+?)\s+for\s+(?:(?P<year1>\d{4})\s+(?P<month1>[A-Za-z]+)|(?P<month2>[A-Za-z]+)\s+(?P<year2>\d{4}))\b",
    re.IGNORECASE,
)

_MONTH_TO_NUMBER = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


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


def _normalize_column_token(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def _extract_requested_group_term(question: str) -> str:
    match = _GROUP_BY_PATTERN.search(question or "")
    if not match:
        return ""
    term = (match.group(1) or "").strip(" .,:;\n\t")
    return term


def _match_column_name(requested_term: str, columns: list[str]) -> str:
    if not requested_term or not columns:
        return ""

    normalized_columns = {_normalize_column_token(col): col for col in columns}
    requested = _normalize_column_token(requested_term)

    # Exact match first.
    if requested in normalized_columns:
        return normalized_columns[requested]

    # Handle singular/plural mismatch like "Business Units" vs "Business Unit".
    if requested.endswith("s") and requested[:-1] in normalized_columns:
        return normalized_columns[requested[:-1]]

    # Fuzzy fallback for mild misspellings.
    close = get_close_matches(requested, list(normalized_columns.keys()), n=1, cutoff=0.72)
    if close:
        return normalized_columns[close[0]]

    return ""


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


def _to_numeric_series(series: pd.Series) -> pd.Series:
    normalized = (
        series.astype(str)
        .str.replace(",", "", regex=False)
        .str.replace("$", "", regex=False)
        .str.replace(r"\(([^\)]+)\)", r"-\1", regex=True)
        .str.strip()
    )
    return pd.to_numeric(normalized, errors="coerce")


def _find_column_by_tokens(columns: list[str], token_groups: list[tuple[str, ...]]) -> str:
    normalized = {col: _normalize_column_token(col) for col in columns}
    for col, norm in normalized.items():
        for group in token_groups:
            if all(token in norm for token in group):
                return col
    return ""


def _extract_month_year(question: str) -> tuple[int | None, int | None]:
    match = _FINANCE_TOTAL_PATTERN.search(question or "")
    if not match:
        return None, None

    year_text = match.group("year1") or match.group("year2")
    month_text = match.group("month1") or match.group("month2")

    year = int(year_text) if year_text else None
    month = _MONTH_TO_NUMBER.get((month_text or "").strip().lower())
    return year, month


def _extract_account_name(question: str) -> str:
    match = _FINANCE_TOTAL_PATTERN.search(question or "")
    if not match:
        return ""
    account = (match.group("account") or "").strip(" .,:;\n\t")
    return account


def _extract_account_and_dimension_values(question: str) -> tuple[str, list[str]]:
    raw_account = _extract_account_name(question)
    if not raw_account:
        return "", []

    parts = re.split(r"\s+for\s+", raw_account, maxsplit=1, flags=re.IGNORECASE)
    account_name = (parts[0] or "").strip(" .,:;\n\t")

    if len(parts) == 1:
        return account_name, []

    dimension_text = (parts[1] or "").strip(" .,:;\n\t")
    if not dimension_text:
        return account_name, []

    values = [
        token.strip(" .,:;\n\t")
        for token in re.split(r"\s*(?:,|and|&|/)\s*", dimension_text, flags=re.IGNORECASE)
        if token and token.strip(" .,:;\n\t")
    ]
    return account_name, values


def _find_month_amount_column(columns: list[str], month: int) -> str:
    month_labels = [
        name
        for name, idx in _MONTH_TO_NUMBER.items()
        if idx == month
    ]
    normalized_candidates = {_normalize_column_token(col): col for col in columns}

    for label in month_labels:
        if label in normalized_candidates:
            return normalized_candidates[label]

    return ""


def _format_currency_accounting(value: float) -> str:
    rounded = round(float(value), 2)
    if abs(rounded - round(rounded)) < 1e-9:
        amount = f"{abs(int(round(rounded))):,}"
    else:
        amount = f"{abs(rounded):,.2f}"

    if rounded < 0:
        return f"(${amount})"
    return f"${amount}"


def _pick_dimension_column(df: pd.DataFrame, base_mask: pd.Series, account_col: str, dimension_values: list[str]) -> str:
    if not dimension_values:
        return ""

    candidates: list[str] = []
    for col in df.columns:
        col_name = str(col)
        if col_name == account_col:
            continue
        if not (df[col_name].dtype == object or str(df[col_name].dtype).startswith("string")):
            continue
        normalized = _normalize_column_token(col_name)
        if normalized in {"scenario", "currency"}:
            continue
        candidates.append(col_name)

    if not candidates:
        return ""

    best_col = ""
    best_terms = -1
    best_hits = -1

    scoped = df[base_mask]
    for col in candidates:
        series = scoped[col].astype(str).str.lower()
        term_hits = 0
        total_hits = 0
        for value in dimension_values:
            token = value.lower()
            hits = int(series.str.contains(re.escape(token), na=False).sum())
            if hits > 0:
                term_hits += 1
                total_hits += hits
        if term_hits > best_terms or (term_hits == best_terms and total_hits > best_hits):
            best_col = col
            best_terms = term_hits
            best_hits = total_hits

    if best_terms <= 0:
        return ""
    return best_col


def _deterministic_finance_total_answer(df: pd.DataFrame, question: str, wants_table: bool) -> str:
    account_name, dimension_values = _extract_account_and_dimension_values(question)
    year, month = _extract_month_year(question)
    if not account_name or not year or not month:
        return ""

    working = df.copy()
    columns = [str(col) for col in working.columns]
    if not columns:
        return ""

    account_col = _find_column_by_tokens(
        columns,
        [
            ("account",),
            ("category",),
            ("ledger",),
            ("gl",),
            ("description",),
            ("coa",),
        ],
    )

    year_col = _find_column_by_tokens(columns, [("year",), ("fiscal", "year")])
    month_col = _find_column_by_tokens(columns, [("month",), ("period",)])
    date_col = _find_column_by_tokens(columns, [("date",), ("posting", "date"), ("transaction", "date")])

    amount_col = _find_column_by_tokens(
        columns,
        [
            ("amount",),
            ("spent",),
            ("expense",),
            ("cost",),
            ("debit",),
            ("value",),
            ("total",),
        ],
    )

    month_amount_col = _find_month_amount_column(columns, month)
    if month_amount_col:
        amount_col = month_amount_col

    if not amount_col:
        numeric_candidates = []
        for col in columns:
            numeric = _to_numeric_series(working[col])
            if numeric.notna().sum() > 0:
                numeric_candidates.append((col, numeric.notna().sum()))
        if numeric_candidates:
            numeric_candidates.sort(key=lambda item: item[1], reverse=True)
            amount_col = numeric_candidates[0][0]

    if not amount_col:
        return ""

    mask = pd.Series(True, index=working.index)

    if account_col:
        account_series = working[account_col].astype(str).str.strip().str.lower()
        account_token = account_name.strip().lower()
        mask &= account_series.str.contains(re.escape(account_token), na=False)
    else:
        account_token = account_name.strip().lower()
        text_columns = [
            col
            for col in columns
            if working[col].dtype == object or str(working[col].dtype).startswith("string")
        ]
        if text_columns:
            account_mask = pd.Series(False, index=working.index)
            for col in text_columns:
                col_mask = working[col].astype(str).str.lower().str.contains(re.escape(account_token), na=False)
                account_mask |= col_mask
            mask &= account_mask

    if year_col:
        year_series = pd.to_numeric(working[year_col].astype(str).str.extract(r"(\d{4})", expand=False), errors="coerce")
        mask &= year_series == year

    if month_col and not month_amount_col:
        month_raw = working[month_col].astype(str).str.strip().str.lower()
        month_num = pd.to_numeric(month_raw, errors="coerce")
        month_name_num = month_raw.map(_MONTH_TO_NUMBER)
        month_effective = month_num.where(month_num.notna(), pd.to_numeric(month_name_num, errors="coerce"))
        mask &= month_effective == month

    if date_col and (not year_col or not month_col):
        parsed_dates = pd.to_datetime(working[date_col], errors="coerce")
        if not year_col:
            mask &= parsed_dates.dt.year == year
        if not month_col:
            mask &= parsed_dates.dt.month == month

    filtered = working[mask].copy()
    if filtered.empty:
        return "No matching data was found."

    amount_values = _to_numeric_series(filtered[amount_col]).fillna(0)

    if dimension_values:
        dimension_col = _pick_dimension_column(working, mask, account_col, dimension_values)
        if not dimension_col:
            return "No matching data was found."

        rows: list[dict[str, Any]] = []
        for value in dimension_values:
            value_mask = mask & working[dimension_col].astype(str).str.lower().str.contains(
                re.escape(value.lower()), na=False
            )
            value_filtered = working[value_mask]
            if value_filtered.empty:
                continue

            value_total = float(_to_numeric_series(value_filtered[amount_col]).fillna(0).sum())
            rows.append(
                {
                    "Account": account_name,
                    "Category": value,
                    "Year": year,
                    "Month": next(
                        (name.capitalize() for name, idx in _MONTH_TO_NUMBER.items() if idx == month and len(name) > 3),
                        str(month),
                    ),
                    "Total Spent": _format_currency_accounting(value_total),
                }
            )

        if not rows:
            return "No matching data was found."

        dimension_df = pd.DataFrame(rows)
        return dimension_df.to_markdown(index=False)

    total = float(amount_values.abs().sum())

    month_name = next((name.capitalize() for name, idx in _MONTH_TO_NUMBER.items() if idx == month and len(name) > 3), str(month))

    table_df = pd.DataFrame(
        [
            {
                "Account": account_name,
                "Year": year,
                "Month": month_name,
                "Total Spent": round(total, 2),
            }
        ]
    )

    if wants_table:
        return table_df.to_markdown(index=False)

    return f"Total spent on {account_name} for {month_name} {year}: {total:,.2f}"


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


def _steps_contain_invalid_tool_error(steps: list[dict[str, Any]]) -> bool:
    for step in steps or []:
        obs = str(step.get("observation", "")).lower()
        if "not a valid tool" in obs or "try one of" in obs:
            return True
    return False


def _build_execution_forcing_question(df: pd.DataFrame, question: str, *, prefer_table: bool = False) -> str:
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

    columns = [str(col) for col in df.columns]
    column_hint = ""
    if columns:
        column_hint = " Available columns in df are: " + ", ".join(columns) + "."

    grouping_hint = ""
    requested_group_term = _extract_requested_group_term(question)
    matched_group_column = _match_column_name(requested_group_term, columns)
    if matched_group_column:
        grouping_hint = (
            f" The user explicitly requested splitting/grouping by '{matched_group_column}'. "
            f"Group by '{matched_group_column}' and return one row per distinct value in that column. "
            "Treat other terms in the question as filters or measures, not as replacement group dimensions."
        )

    return (
        "Use Python on the provided pandas DataFrame `df` and compute the final result now. "
        "The only runnable tool is python_repl_ast; never call pandas methods (like to_dict/to_markdown) as tools. "
        "If you need to_dict/to_markdown, use them inside Python code executed by python_repl_ast. "
        "Always use the actual column names from `df` (case-insensitive matching is fine in code, but preserve canonical labels in output). "
        "Do not describe your plan or steps. Return only the final answer with computed values. "
        f"If grouped totals are requested, include each group and its numeric total.{spend_hint}{format_hint}{column_hint}{grouping_hint}\n\n"
        f"Question: {question}"
    )


def query_dataframe_with_langchain(df: pd.DataFrame, question: str) -> dict[str, Any]:
    if df.empty:
        raise HTTPException(status_code=400, detail="DataFrame has no rows to analyze.")

    normalized_question = (question or "").strip()
    if not normalized_question:
        raise HTTPException(status_code=400, detail="Question cannot be empty.")

    wants_table = _is_table_request(normalized_question)

    deterministic_answer = _deterministic_finance_total_answer(df, normalized_question, wants_table=wants_table)
    if deterministic_answer:
        return {
            "success": True,
            "answer": deterministic_answer,
            "intermediate_steps": [],
        }

    if create_pandas_dataframe_agent is None:
        raise HTTPException(
            status_code=500,
            detail=(
                "langchain_experimental is required for DataFrame querying. "
                "Install langchain-experimental in the backend environment."
            ),
        )

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

    forced_question = _build_execution_forcing_question(df, normalized_question, prefer_table=wants_table)

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

    had_invalid_tool_error = _steps_contain_invalid_tool_error(steps)
    if not answer and had_invalid_tool_error:
        invalid_tool_retry_question = (
            "Retry and execute the computation now using python_repl_ast only. "
            "Do not call to_dict/to_markdown as tools; they are pandas methods used inside Python code. "
            "Return only the final computed answer. "
            "If grouped totals are requested, include each group and its total.\n\n"
            f"Question: {normalized_question}"
        )
        try:
            invalid_tool_retry_result = agent.invoke({"input": invalid_tool_retry_question})
        except Exception:
            invalid_tool_retry_result = None

        if isinstance(invalid_tool_retry_result, dict):
            invalid_tool_retry_answer = str(invalid_tool_retry_result.get("output", "")).strip()
            invalid_tool_retry_steps = _serialize_intermediate_steps(invalid_tool_retry_result.get("intermediate_steps"))
            if invalid_tool_retry_answer:
                answer = invalid_tool_retry_answer
                steps = invalid_tool_retry_steps
        elif invalid_tool_retry_result is not None:
            invalid_tool_retry_answer = str(invalid_tool_retry_result).strip()
            if invalid_tool_retry_answer:
                answer = invalid_tool_retry_answer

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
            df,
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
        if _steps_contain_execution_error(steps) or _steps_contain_invalid_tool_error(steps):
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
