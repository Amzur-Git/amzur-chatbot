from __future__ import annotations

from typing import Any, Optional

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import get_current_user, get_db
from app.models.user import User
from app.schemas.sheets import (
    DataFrameInfo,
    SheetsLoadPreviewRequest,
    SheetsLoadPreviewResponse,
    SheetsPreviewData,
    SheetsQueryFileRequest,
    SheetsQueryGoogleSheetRequest,
    SheetsQueryResponse,
)
from app.services.attachment_service import AttachmentService
from app.services.sheets_oauth_service import (
    has_user_google_oauth,
    load_sheet_as_dataframe_with_metadata_for_user,
)
from app.services.sheets_query_service import query_dataframe_with_langchain
from app.services.sheets_service import load_file_as_dataframe, load_sheet_as_dataframe_with_metadata

router = APIRouter(prefix="/api/sheets", tags=["sheets"])


def _service_account_fallback_configured() -> bool:
    return bool(
        (settings.GOOGLE_SERVICE_ACCOUNT_JSON or "").strip()
        or (settings.GOOGLE_SERVICE_ACCOUNT_FILE or "").strip()
    )


def _build_dataframe_info(df: pd.DataFrame) -> DataFrameInfo:
    return DataFrameInfo(
        rows=int(df.shape[0]),
        columns=int(df.shape[1]),
        column_names=[str(col) for col in df.columns.tolist()],
    )


def _build_preview(df: pd.DataFrame) -> SheetsPreviewData:
    preview_frame = df.head(5).where(pd.notnull(df.head(5)), None)
    head: list[dict[str, Any]] = jsonable_encoder(preview_frame.to_dict(orient="records"))

    return SheetsPreviewData(
        head=head,
        shape=[int(df.shape[0]), int(df.shape[1])],
        columns=[str(col) for col in df.columns.tolist()],
        dtypes={str(col): str(dtype) for col, dtype in df.dtypes.items()},
    )


@router.post("/query-file", response_model=SheetsQueryResponse)
async def query_uploaded_file(
    request: SheetsQueryFileRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> SheetsQueryResponse:
    attachment = await AttachmentService.get_by_id(db, current_user, request.file_id)

    if attachment.file_type != "table":
        raise HTTPException(status_code=400, detail="Selected file is not a CSV/Excel table attachment.")

    df = load_file_as_dataframe(attachment.file_path)
    query_result = query_dataframe_with_langchain(df, request.question)

    return SheetsQueryResponse(
        success=bool(query_result.get("success", True)),
        answer=str(query_result["answer"]),
        intermediate_steps=query_result.get("intermediate_steps", []),
        dataframe_info=_build_dataframe_info(df),
    )


@router.post("/query-google-sheet", response_model=SheetsQueryResponse)
async def query_google_sheet(
    request: SheetsQueryGoogleSheetRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> SheetsQueryResponse:
    if has_user_google_oauth(current_user):
        df, sheet_name = await load_sheet_as_dataframe_with_metadata_for_user(db, current_user, request.sheet_url)
    elif _service_account_fallback_configured():
        # Backward-compatible fallback for environments still using a shared service account.
        df, sheet_name = load_sheet_as_dataframe_with_metadata(request.sheet_url)
    else:
        raise HTTPException(
            status_code=403,
            detail="Google Sheets is not linked for this account. Sign in with Google to authorize Sheets access.",
        )

    query_result = query_dataframe_with_langchain(df, request.question)

    return SheetsQueryResponse(
        success=bool(query_result.get("success", True)),
        answer=str(query_result["answer"]),
        intermediate_steps=query_result.get("intermediate_steps", []),
        sheet_name=sheet_name,
        dataframe_info=_build_dataframe_info(df),
    )


@router.post("/load-preview", response_model=SheetsLoadPreviewResponse)
async def load_sheet_preview(
    request: SheetsLoadPreviewRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> SheetsLoadPreviewResponse:
    df: Optional[pd.DataFrame] = None
    sheet_name: Optional[str] = None

    if request.sheet_url:
        if has_user_google_oauth(current_user):
            df, sheet_name = await load_sheet_as_dataframe_with_metadata_for_user(
                db,
                current_user,
                request.sheet_url,
            )
        elif _service_account_fallback_configured():
            # Backward-compatible fallback for environments still using a shared service account.
            df, sheet_name = load_sheet_as_dataframe_with_metadata(request.sheet_url)
        else:
            raise HTTPException(
                status_code=403,
                detail="Google Sheets is not linked for this account. Sign in with Google to authorize Sheets access.",
            )
    elif request.file_id:
        attachment = await AttachmentService.get_by_id(db, current_user, request.file_id)
        if attachment.file_type != "table":
            raise HTTPException(status_code=400, detail="Selected file is not a CSV/Excel table attachment.")
        df = load_file_as_dataframe(attachment.file_path)

    if df is None:
        raise HTTPException(status_code=400, detail="No data source provided for preview.")

    return SheetsLoadPreviewResponse(
        success=True,
        sheet_name=sheet_name,
        dataframe_info=_build_dataframe_info(df),
        preview=_build_preview(df),
    )
