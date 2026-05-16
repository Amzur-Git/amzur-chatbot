from __future__ import annotations

from typing import Any, Optional

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy.ext.asyncio import AsyncSession

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
from app.services.chat_service import ChatService
from app.services.sheets_oauth_service import (
    diagnose_google_sheet_access_for_user,
    has_user_google_oauth,
    load_sheet_as_dataframe_with_metadata_for_user,
)
from app.services.sheets_query_service import query_dataframe_with_langchain
from app.services.sheets_service import (
    load_sheet_as_dataframe_with_metadata,
    load_file_as_dataframe,
    load_public_sheet_as_dataframe_with_metadata,
)

router = APIRouter(prefix="/api/sheets", tags=["sheets"])


@router.get("/oauth-diagnose")
async def oauth_diagnose_sheet_access(
    sheet_url: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not has_user_google_oauth(current_user):
        raise HTTPException(
            status_code=403,
            detail="Google OAuth is not connected for this user.",
        )

    return await diagnose_google_sheet_access_for_user(db, current_user, sheet_url)


def _load_sheet_for_non_sso(sheet_url: str) -> tuple[pd.DataFrame, str]:
    """
    Non-SSO users should first use service-account access (private/shared sheets),
    then fall back to public CSV export for publicly shared sheets.
    """
    try:
        return load_sheet_as_dataframe_with_metadata(sheet_url)
    except HTTPException as service_error:
        try:
            return load_public_sheet_as_dataframe_with_metadata(sheet_url)
        except HTTPException as public_error:
            # If both paths are forbidden, keep service-account guidance first.
            if service_error.status_code == 403 and public_error.status_code == 403:
                raise service_error
            # If service account is unavailable/misconfigured, keep that detail when
            # public export is also forbidden so users get the right remediation path.
            if service_error.status_code >= 500:
                if public_error.status_code == 403:
                    raise HTTPException(
                        status_code=service_error.status_code,
                        detail=(
                            f"{service_error.detail} "
                            "Public CSV export is also blocked (403). "
                            "For private sheets, share the sheet with the service account "
                            "email or connect Google OAuth."
                        ),
                    ) from public_error
                raise public_error
            raise


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


async def _persist_sheets_chat_if_threaded(
    db: AsyncSession,
    current_user: User,
    chat_thread_id: Optional[Any],
    question: str,
    answer: str,
) -> Any:
    thread = None
    if chat_thread_id:
        thread = await ChatService.get_thread(db, current_user.id, chat_thread_id)
        if not thread:
            raise HTTPException(status_code=404, detail="Thread not found")
    else:
        existing_threads = await ChatService.list_threads(db, current_user.id)
        thread = existing_threads[0] if existing_threads else await ChatService.create_thread(db, current_user.id)

    await ChatService.save_message(
        db,
        current_user.id,
        "user",
        question,
        thread_id=thread.id,
    )
    await ChatService.ensure_thread_title(db, thread, question)
    await ChatService.save_message(
        db,
        current_user.id,
        "assistant",
        answer,
        thread_id=thread.id,
    )

    return thread.id


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
    answer = str(query_result["answer"])

    persisted_thread_id = await _persist_sheets_chat_if_threaded(
        db,
        current_user,
        request.chat_thread_id,
        request.question,
        answer,
    )

    return SheetsQueryResponse(
        success=bool(query_result.get("success", True)),
        answer=answer,
        intermediate_steps=query_result.get("intermediate_steps", []),
        thread_id=persisted_thread_id,
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
    else:
        df, sheet_name = _load_sheet_for_non_sso(request.sheet_url)

    query_result = query_dataframe_with_langchain(df, request.question)
    answer = str(query_result["answer"])

    persisted_thread_id = await _persist_sheets_chat_if_threaded(
        db,
        current_user,
        request.chat_thread_id,
        request.question,
        answer,
    )

    return SheetsQueryResponse(
        success=bool(query_result.get("success", True)),
        answer=answer,
        intermediate_steps=query_result.get("intermediate_steps", []),
        sheet_name=sheet_name,
        thread_id=persisted_thread_id,
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
        else:
            df, sheet_name = _load_sheet_for_non_sso(request.sheet_url)
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
