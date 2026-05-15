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
