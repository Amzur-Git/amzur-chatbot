import pandas as pd

from app.services import sheets_query_service


class FakeAgent:
    def __init__(self, responses):
        self._responses = list(responses)
        self.inputs = []

    def invoke(self, payload):
        self.inputs.append(payload["input"])
        if not self._responses:
            return {"output": "", "intermediate_steps": []}
        return self._responses.pop(0)


class _FakeAction:
    tool = "python_repl_ast"
    tool_input = "print('x')"
    log = "log"


def _df():
    return pd.DataFrame(
        {
            "Year": [2012, 2012],
            "Businees Unit": ["Software", "Hardware"],
            "Account": ["Sales", "Sales"],
            "Jan": [1, 2],
        }
    )


def test_table_request_recovers_after_execution_error(monkeypatch):
    syntax_error_steps = [(_FakeAction(), "SyntaxError: unterminated string literal")]
    fake_agent = FakeAgent(
        [
            {"output": "", "intermediate_steps": syntax_error_steps},
            {"output": "", "intermediate_steps": []},
            {"output": "", "intermediate_steps": []},
            {
                "output": "| Business Unit | Annual Sales |\n|---|---|\n| Software | 9.61647e+08 |\n| Hardware | -2.1111e+08 |",
                "intermediate_steps": [],
            },
        ]
    )

    monkeypatch.setattr(
        sheets_query_service,
        "create_pandas_dataframe_agent",
        lambda **kwargs: fake_agent,
    )

    result = sheets_query_service.query_dataframe_with_langchain(
        _df(),
        "Represent the data in a table format",
    )

    assert result["success"] is True
    assert "| Business Unit |" in result["answer"]
    assert "9.61647e+08" not in result["answer"]
    assert "-2.1111e+08" not in result["answer"]
    assert "961,647,000" in result["answer"]
    assert "-211,110,000" in result["answer"]
    assert len(fake_agent.inputs) == 4


def test_empty_outputs_return_no_data_message(monkeypatch):
    fake_agent = FakeAgent(
        [
            {"output": "", "intermediate_steps": []},
            {"output": "", "intermediate_steps": []},
        ]
    )

    monkeypatch.setattr(
        sheets_query_service,
        "create_pandas_dataframe_agent",
        lambda **kwargs: fake_agent,
    )

    result = sheets_query_service.query_dataframe_with_langchain(
        _df(),
        "Show spend for year 2099",
    )

    assert result["success"] is True
    assert result["answer"] == "No matching data found for your query in the current sheet."
    assert len(fake_agent.inputs) == 2


def test_group_by_term_matching_handles_plural_and_typo_column_name():
    columns = ["Year", "Businees Unit", "Expense", "Department"]

    requested = sheets_query_service._extract_requested_group_term(
        "Show expense for 2015 split the data based on Business Units"
    )
    matched = sheets_query_service._match_column_name(requested, columns)

    assert requested.lower() == "business units"
    assert matched == "Businees Unit"


def test_execution_prompt_includes_explicit_grouping_hint(monkeypatch):
    fake_agent = FakeAgent([{"output": "ok", "intermediate_steps": []}])

    monkeypatch.setattr(
        sheets_query_service,
        "create_pandas_dataframe_agent",
        lambda **kwargs: fake_agent,
    )

    df = pd.DataFrame(
        {
            "Year": [2015],
            "Businees Unit": ["Advertising"],
            "Travel & Entertainment Expense": [100],
        }
    )

    sheets_query_service.query_dataframe_with_langchain(
        df,
        "Find the Travel & Entertainment Expense for Advertising for 2015, show it in a table format split the data based on Business Units",
    )

    assert len(fake_agent.inputs) == 1
    prompt = fake_agent.inputs[0]
    assert "Available columns in df are" in prompt
    assert "explicitly requested splitting/grouping" in prompt
    assert "Group by 'Businees Unit'" in prompt


def test_invalid_tool_observation_retries_and_returns_answer(monkeypatch):
    invalid_tool_steps = [(_FakeAction(), "to_dict is not a valid tool, try one of [python_repl_ast].")]
    fake_agent = FakeAgent(
        [
            {"output": "", "intermediate_steps": invalid_tool_steps},
            {
                "output": "| Businees Unit | Total Expense |\n|---|---|\n| Software | 100 |\n| Hardware | 200 |\n\nTotal: 300",
                "intermediate_steps": [],
            },
        ]
    )

    monkeypatch.setattr(
        sheets_query_service,
        "create_pandas_dataframe_agent",
        lambda **kwargs: fake_agent,
    )

    result = sheets_query_service.query_dataframe_with_langchain(
        _df(),
        "Find the total expense of Sales account for the year 2012 and split by business unit in a table format",
    )

    assert result["success"] is True
    assert "to_dict is not a valid tool" not in result["answer"]
    assert "| Businees Unit | Total Expense |" in result["answer"]
    assert "Total: 300" in result["answer"]
    assert len(fake_agent.inputs) == 2
