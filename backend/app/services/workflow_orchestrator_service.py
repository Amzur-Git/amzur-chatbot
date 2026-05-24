from __future__ import annotations

import asyncio
from datetime import datetime
import json
from typing import Any
import urllib.request

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm import llm
from app.core.config import settings
from app.models.user import User
from app.models.workflow_run import WorkflowRun
from app.schemas.workflow import WorkflowRunRequest
from app.services.attachment_service import AttachmentService
from app.services.chat_service import ChatService
from app.services.image_generation_service import ImageGenerationService
from app.services.research_digest_service import ResearchDigestService


class WorkflowOrchestratorService:
    @staticmethod
    def _utcnow() -> datetime:
        return datetime.utcnow()

    @staticmethod
    def _pick_topic(payload: WorkflowRunRequest) -> str:
        if payload.topic and payload.topic.strip():
            return payload.topic.strip()
        return payload.user_request.strip()[:300]

    @staticmethod
    def _parse_sse_event(raw_event: str) -> tuple[str, dict[str, Any]] | None:
        event_name: str | None = None
        data_lines: list[str] = []

        for line in raw_event.strip().splitlines():
            if line.startswith("event:"):
                event_name = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                data_lines.append(line.split(":", 1)[1].strip())

        if not event_name:
            return None

        raw_data = "\n".join(data_lines).strip() if data_lines else "{}"
        try:
            parsed_data = json.loads(raw_data)
        except json.JSONDecodeError:
            parsed_data = {"raw": raw_data}

        return event_name, parsed_data if isinstance(parsed_data, dict) else {"value": parsed_data}

    @classmethod
    async def _generate_research_digest(
        cls,
        *,
        topic: str,
        max_rounds: int,
        papers_per_round: int,
        min_papers: int,
    ) -> tuple[str | None, dict[str, Any]]:
        digest_text: str | None = None
        summary: dict[str, Any] = {
            "papers_seen": 0,
            "rounds_completed": 0,
            "events": [],
        }

        async for raw_event in ResearchDigestService.stream_digest(
            topic=topic,
            max_rounds=max_rounds,
            papers_per_round=papers_per_round,
            min_papers=min_papers,
        ):
            parsed = cls._parse_sse_event(raw_event)
            if not parsed:
                continue

            event_name, data = parsed
            summary["events"].append(event_name)

            if event_name == "papers":
                papers = data.get("papers")
                if isinstance(papers, list):
                    summary["papers_seen"] = max(summary["papers_seen"], len(papers))
            elif event_name == "decision":
                rounds = data.get("round")
                if isinstance(rounds, int):
                    summary["rounds_completed"] = max(summary["rounds_completed"], rounds)
            elif event_name == "final_digest":
                candidate = data.get("digest")
                if isinstance(candidate, str) and candidate.strip():
                    digest_text = candidate.strip()

        return digest_text, summary

    @staticmethod
    def _build_image_prompt(*, topic: str, digest_text: str, explicit_prompt: str | None) -> str:
        if explicit_prompt and explicit_prompt.strip():
            return explicit_prompt.strip()

        prompt = (
            "Extract one strong visual concept from this research digest and rewrite it as a concise image prompt. "
            "Return only the final prompt text with no extra explanation.\n\n"
            f"Topic: {topic}\n\n"
            f"Digest:\n{digest_text[:3500]}"
        )
        response = llm.invoke(prompt)
        result = ResearchDigestService._response_text(response).strip()
        return result or f"A clean editorial illustration representing: {topic}"

    @staticmethod
    async def _send_slack_message(webhook_url: str, text: str) -> None:
        data = json.dumps({"text": text}).encode("utf-8")
        request = urllib.request.Request(
            webhook_url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        def _post() -> None:
            with urllib.request.urlopen(request, timeout=15) as response:  # nosec B310 - URL is user/env supplied webhook.
                if response.status >= 400:
                    raise RuntimeError(f"Slack webhook returned status {response.status}")

        await asyncio.to_thread(_post)

    @staticmethod
    async def trigger_sheets_email_workflow(*, payload: dict[str, Any]) -> dict[str, Any]:
        webhook_url = settings.N8N_EXCEL_QUERY_EMAIL_WEBHOOK_URL
        if not webhook_url:
            raise HTTPException(
                status_code=503,
                detail="Sheets email workflow is not configured (missing N8N webhook URL).",
            )

        timeout_seconds = max(1, settings.N8N_EXCEL_QUERY_EMAIL_TIMEOUT_SECONDS)
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            webhook_url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        def _post() -> tuple[int, str]:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # nosec B310 - URL is env configured webhook.
                status = int(getattr(response, "status", 200))
                response_body = response.read().decode("utf-8", errors="replace")
                return status, response_body

        try:
            status, response_body = await asyncio.to_thread(_post)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Failed to reach n8n workflow webhook: {exc}") from exc

        if status >= 400:
            raise HTTPException(
                status_code=502,
                detail=f"n8n workflow webhook returned status {status}: {response_body[:400]}",
            )

        try:
            parsed = json.loads(response_body) if response_body.strip() else {}
            return parsed if isinstance(parsed, dict) else {"value": parsed}
        except json.JSONDecodeError:
            return {"raw": response_body}

    @classmethod
    async def run(
        cls,
        *,
        db: AsyncSession,
        user: User,
        payload: WorkflowRunRequest,
    ) -> dict[str, Any]:
        topic = cls._pick_topic(payload)
        thread = await ChatService.get_thread(db, user.id, payload.chat_thread_id)
        if not thread:
            raise HTTPException(status_code=404, detail="Thread not found")

        run = WorkflowRun(
            user_id=user.id,
            thread_id=thread.id,
            user_request=payload.user_request,
            topic=topic,
            status="running",
            started_at=cls._utcnow(),
            step_results={},
            error_messages=[],
        )
        db.add(run)
        await db.commit()
        await db.refresh(run)

        step_results: dict[str, dict[str, Any]] = {}
        errors: list[str] = []

        digest_text = ""
        digest_message_id = None
        image_message_id = None
        image_attachment_id = None
        image_prompt = None

        user_message_id = None
        try:
            user_message = await ChatService.save_message(
                db,
                thread_id=thread.id,
                role="user",
                content=payload.user_request,
            )
            user_message_id = user_message.id
            step_results["capture_user_request"] = {
                "status": "success",
                "message": "User request saved in thread.",
                "metadata": {"message_id": str(user_message_id)},
            }
        except Exception as exc:
            errors.append(f"Failed to store user request message: {exc}")
            step_results["capture_user_request"] = {
                "status": "failed",
                "message": str(exc),
                "metadata": {},
            }

        try:
            digest_text_or_none, digest_meta = await cls._generate_research_digest(
                topic=topic,
                max_rounds=payload.max_rounds,
                papers_per_round=payload.papers_per_round,
                min_papers=payload.min_papers,
            )
            if digest_text_or_none and digest_text_or_none.strip():
                digest_text = digest_text_or_none.strip()
                step_results["research_digest"] = {
                    "status": "success",
                    "message": "Research digest generated.",
                    "metadata": digest_meta,
                }
            else:
                digest_text = (
                    "Unable to generate a full research digest for this run. "
                    "The workflow continued with a fallback summary."
                )
                step_results["research_digest"] = {
                    "status": "failed",
                    "message": "No final digest returned.",
                    "metadata": digest_meta,
                }
                errors.append("Research digest did not produce final output.")
        except Exception as exc:
            digest_text = (
                "Research digest failed in this run, but the workflow continued. "
                f"Reason: {exc}"
            )
            step_results["research_digest"] = {
                "status": "failed",
                "message": str(exc),
                "metadata": {},
            }
            errors.append(f"Research digest step failed: {exc}")

        try:
            digest_message = await ChatService.save_message(
                db,
                thread_id=thread.id,
                role="assistant",
                content=digest_text,
                parent_message_id=user_message_id,
            )
            digest_message_id = digest_message.id
            step_results["store_digest_message"] = {
                "status": "success",
                "message": "Digest stored as assistant message.",
                "metadata": {"message_id": str(digest_message_id)},
            }
        except Exception as exc:
            step_results["store_digest_message"] = {
                "status": "failed",
                "message": str(exc),
                "metadata": {},
            }
            errors.append(f"Storing digest message failed: {exc}")

        try:
            image_prompt = cls._build_image_prompt(
                topic=topic,
                digest_text=digest_text,
                explicit_prompt=payload.image_prompt,
            )

            generated_images = await ImageGenerationService.generate_images(
                user_key=str(user.id),
                prompt=image_prompt,
                num_images=payload.num_images,
                aspect_ratio=payload.aspect_ratio,
            )
            first_image = generated_images[0]

            image_message = await ChatService.save_message(
                db,
                thread_id=thread.id,
                role="assistant",
                content="Generated a visual concept from the research digest.",
                parent_message_id=digest_message_id or user_message_id,
            )
            image_message_id = image_message.id

            attachment = await AttachmentService.create_generated_image_attachment(
                db=db,
                user=user,
                thread_id=thread.id,
                bytes_data=first_image.bytes_data,
                mime_type=first_image.mime_type,
                prompt=image_prompt,
                model_version=first_image.model_version,
                aspect_ratio=payload.aspect_ratio,
                auto_commit=True,
            )
            image_attachment_id = attachment.id

            await AttachmentService.attach_to_message(
                db=db,
                user=user,
                thread_id=thread.id,
                message_id=image_message_id,
                attachment_ids=[image_attachment_id],
            )

            step_results["generate_image"] = {
                "status": "success",
                "message": "Generated and attached image.",
                "metadata": {
                    "message_id": str(image_message_id),
                    "attachment_id": str(image_attachment_id),
                },
            }
        except Exception as exc:
            errors.append(f"Image generation failed: {exc}")
            try:
                fallback_message = await ChatService.save_message(
                    db,
                    thread_id=thread.id,
                    role="assistant",
                    content=(
                        "I could not generate an image for this digest in this run. "
                        "The text summary is still available above."
                    ),
                    parent_message_id=digest_message_id or user_message_id,
                )
                image_message_id = fallback_message.id
            except Exception:
                pass

            step_results["generate_image"] = {
                "status": "failed",
                "message": str(exc),
                "metadata": {},
            }

        if payload.send_slack_dm:
            webhook_url = (payload.slack_webhook_url or settings.WORKFLOW_SLACK_WEBHOOK_URL or "").strip()
            if not webhook_url:
                step_results["slack_dm"] = {
                    "status": "failed",
                    "message": "Slack webhook URL not configured.",
                    "metadata": {},
                }
                errors.append("Slack DM skipped: missing webhook URL.")
            else:
                try:
                    snippet = digest_text[:1200]
                    await cls._send_slack_message(
                        webhook_url,
                        f"Research workflow completed for topic: {topic}\n\n{snippet}",
                    )
                    step_results["slack_dm"] = {
                        "status": "success",
                        "message": "Slack message delivered.",
                        "metadata": {},
                    }
                except Exception as exc:
                    step_results["slack_dm"] = {
                        "status": "failed",
                        "message": str(exc),
                        "metadata": {},
                    }
                    errors.append(f"Slack delivery failed: {exc}")
        else:
            step_results["slack_dm"] = {
                "status": "skipped",
                "message": "Slack delivery not requested.",
                "metadata": {},
            }

        final_status = "completed" if not errors else "completed_with_errors"
        run.status = final_status
        run.step_results = step_results
        run.error_messages = errors
        run.digest_text = digest_text
        run.digest_message_id = digest_message_id
        run.image_prompt = image_prompt
        run.image_message_id = image_message_id
        run.image_attachment_id = image_attachment_id
        run.slack_delivery_status = step_results.get("slack_dm", {}).get("status")
        run.completed_at = cls._utcnow()

        await db.commit()
        await db.refresh(run)

        return {
            "run_id": run.id,
            "status": run.status,
            "topic": run.topic,
            "digest_message_id": run.digest_message_id,
            "image_message_id": run.image_message_id,
            "image_attachment_id": run.image_attachment_id,
            "step_results": run.step_results or {},
            "errors": run.error_messages or [],
            "completed_at": run.completed_at or cls._utcnow(),
        }
