from __future__ import annotations

import uuid
from typing import Any, Optional

from pydantic import BaseModel, Field, root_validator


class DataFrameInfo(BaseModel):
    rows: int
    columns: int
    column_names: list[str]


class SheetsQueryFileRequest(BaseModel):
    file_id: uuid.UUID
    question: str = Field(..., min_length=1)
    chat_thread_id: Optional[uuid.UUID] = None


class SheetsQueryGoogleSheetRequest(BaseModel):
    sheet_url: str = Field(..., min_length=1)
    question: str = Field(..., min_length=1)
    chat_thread_id: Optional[uuid.UUID] = None


class SheetsLoadPreviewRequest(BaseModel):
    sheet_url: Optional[str] = None
    file_id: Optional[uuid.UUID] = None

    @root_validator(pre=True)
    def validate_source(cls, values: dict[str, Any]) -> dict[str, Any]:
        sheet_url = (values.get("sheet_url") or "").strip()
        file_id = values.get("file_id")

        if not sheet_url and not file_id:
            raise ValueError("Either sheet_url or file_id must be provided.")
        if sheet_url and file_id:
            raise ValueError("Provide only one source: sheet_url or file_id.")

        values["sheet_url"] = sheet_url or None
        return values


class SheetsQueryResponse(BaseModel):
    success: bool
    answer: str
    intermediate_steps: list[dict[str, Any]] = Field(default_factory=list)
    sheet_name: Optional[str] = None
    dataframe_info: DataFrameInfo


class SheetsPreviewData(BaseModel):
    head: list[dict[str, Any]]
    shape: list[int]
    columns: list[str]
    dtypes: dict[str, str]


class SheetsLoadPreviewResponse(BaseModel):
    success: bool
    sheet_name: Optional[str] = None
    dataframe_info: DataFrameInfo
    preview: SheetsPreviewData
