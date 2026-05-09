from __future__ import annotations

import asyncio
import base64
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
import re
import threading
from typing import Any

from fastapi import HTTPException

from app.ai.llm import client
from app.core.config import settings


@dataclass
class GeneratedImage:
    bytes_data: bytes
    mime_type: str
    prompt_used: str
    model_version: str | None


class ImageGenerationService:
    logger = logging.getLogger(__name__)
    _lock = threading.Lock()
    _rate_windows: dict[str, deque[datetime]] = defaultdict(deque)
    _semaphore = asyncio.Semaphore(max(1, settings.IMAGE_GEN_MAX_CONCURRENT_REQUESTS))

    _BLOCKED_PATTERNS = [
        r"\b(child\s*sexual|sexual\s*minor|csam)\b",
        r"\b(explicit\s*gore|extreme\s*violence)\b",
    ]

    @staticmethod
    def sanitize_prompt(prompt: str) -> str:
        cleaned = re.sub(r"\s+", " ", (prompt or "").strip())
        cleaned = "".join(char for char in cleaned if char.isprintable())
        if not cleaned:
            raise HTTPException(status_code=400, detail="Prompt cannot be empty")
        if len(cleaned) > settings.IMAGE_GEN_MAX_PROMPT_CHARS:
            raise HTTPException(
                status_code=400,
                detail=f"Prompt is too long. Max {settings.IMAGE_GEN_MAX_PROMPT_CHARS} characters",
            )
        return cleaned

    @staticmethod
    def check_prompt_safety(prompt: str) -> None:
        lowered = prompt.lower()
        for pattern in ImageGenerationService._BLOCKED_PATTERNS:
            if re.search(pattern, lowered):
                raise HTTPException(
                    status_code=400,
                    detail="Prompt violates content policy for image generation",
                )

    @staticmethod
    def enforce_rate_limit(user_key: str) -> None:
        now = datetime.utcnow()
        window_start = now - timedelta(hours=1)
        limit = max(1, settings.IMAGE_GEN_RATE_LIMIT)

        with ImageGenerationService._lock:
            bucket = ImageGenerationService._rate_windows[user_key]
            while bucket and bucket[0] < window_start:
                bucket.popleft()

            if len(bucket) >= limit:
                raise HTTPException(
                    status_code=429,
                    detail="Image generation rate limit reached. Please try again later",
                )

            bucket.append(now)

    @staticmethod
    async def enhance_prompt(prompt: str) -> str:
        # Keep enhancement deterministic and resilient; this avoids coupling to another model call.
        style_hint = "high detail, clean composition, natural lighting"
        if style_hint.lower() in prompt.lower():
            return prompt
        return f"{prompt}, {style_hint}".strip(" ,")

    @staticmethod
    async def generate_images(
        user_key: str,
        prompt: str,
        num_images: int,
        aspect_ratio: str,
        negative_prompt: str | None = None,
        enhance_prompt: bool = True,
    ) -> list[GeneratedImage]:
        safe_prompt = ImageGenerationService.sanitize_prompt(prompt)
        ImageGenerationService.check_prompt_safety(safe_prompt)
        ImageGenerationService.enforce_rate_limit(user_key)

        total = min(max(1, num_images), settings.IMAGE_GEN_MAX_PER_REQUEST)
        ratio = aspect_ratio or settings.IMAGE_GEN_DEFAULT_ASPECT_RATIO

        effective_prompt = safe_prompt
        if enhance_prompt:
            effective_prompt = await ImageGenerationService.enhance_prompt(safe_prompt)

        if negative_prompt:
            effective_prompt = f"{effective_prompt}. Avoid: {negative_prompt.strip()}"

        async with ImageGenerationService._semaphore:
            tasks = [
                ImageGenerationService._generate_single_image(effective_prompt, ratio)
                for _ in range(total)
            ]
            try:
                return await asyncio.gather(*tasks)
            except HTTPException:
                raise
            except Exception as error:
                ImageGenerationService.logger.exception(
                    "Image generation failed: model=%s prompt_len=%s aspect_ratio=%s",
                    settings.IMAGE_GEN_MODEL,
                    len(effective_prompt),
                    ratio,
                )
                raise ImageGenerationService._map_generation_error(error) from error

    @staticmethod
    async def _generate_single_image(prompt: str, aspect_ratio: str) -> GeneratedImage:
        timeout_seconds = max(20, settings.IMAGE_GEN_TIMEOUT_SECONDS)

        async def _run() -> GeneratedImage:
            return await asyncio.to_thread(ImageGenerationService._generate_single_image_sync, prompt, aspect_ratio)

        try:
            return await asyncio.wait_for(_run(), timeout=timeout_seconds)
        except asyncio.TimeoutError as error:
            raise HTTPException(
                status_code=504,
                detail="Image generation timed out",
            ) from error

    @staticmethod
    def _generate_single_image_sync(prompt: str, aspect_ratio: str) -> GeneratedImage:
        # Keep ratio as textual guidance because provider-specific image params vary by model.
        effective_prompt = prompt
        if aspect_ratio:
            effective_prompt = f"{prompt}. Composition target aspect ratio: {aspect_ratio}."

        response = client.images.generate(
            model=settings.IMAGE_GEN_MODEL,
            prompt=effective_prompt,
        )

        image_bytes, mime_type = ImageGenerationService._extract_image_payload(response)
        model_version = settings.IMAGE_GEN_MODEL

        return GeneratedImage(
            bytes_data=image_bytes,
            mime_type=mime_type,
            prompt_used=prompt,
            model_version=model_version,
        )

    @staticmethod
    def _extract_image_payload(response: Any) -> tuple[bytes, str]:
        # OpenAI-compatible response from LiteLLM generally has response.data[*].b64_json.
        response_data = getattr(response, "data", None) or []

        for item in response_data:
            b64_payload = getattr(item, "b64_json", None)
            if b64_payload:
                return base64.b64decode(b64_payload), "image/png"

            # Fallback for provider responses that include raw bytes-like fields.
            bytes_payload = getattr(item, "image_bytes", None) or getattr(item, "bytes_data", None)
            if isinstance(bytes_payload, bytes):
                return bytes_payload, "image/png"

            # Optional URL responses are not persisted without fetch support.
            image_url = getattr(item, "url", None)
            if image_url:
                raise HTTPException(
                    status_code=502,
                    detail="Image provider returned URL-only payload; b64 image payload is required",
                )

        raise HTTPException(
            status_code=502,
            detail="Model did not return an image payload",
        )

    @staticmethod
    def _map_generation_error(error: Exception) -> HTTPException:
        text = str(error).lower()

        if (
            "quota" in text
            or "rate limit" in text
            or "resource_exhausted" in text
            or "resourceexhausted" in text
            or "exceeded your current quota" in text
            or "429" in text
            or "too many requests" in text
            or "quota exceeded" in text
        ):
            return HTTPException(status_code=429, detail="Image generation quota exceeded via LiteLLM/provider. Please try again later")

        if "api key" in text or "permission" in text or "unauthorized" in text:
            return HTTPException(status_code=401, detail="LiteLLM/provider authentication failed")

        if "not found" in text and "model" in text:
            return HTTPException(
                status_code=502,
                detail=(
                    "Configured image model is unavailable on LiteLLM/provider. "
                    "Set IMAGE_GEN_MODEL to a valid image model"
                ),
            )

        if "safety" in text or "blocked" in text:
            return HTTPException(status_code=400, detail="Prompt blocked by safety filters")

        if "timeout" in text:
            return HTTPException(status_code=504, detail="Image generation timed out")

        return HTTPException(
            status_code=502,
            detail="Image generation failed via LiteLLM/provider",
        )
