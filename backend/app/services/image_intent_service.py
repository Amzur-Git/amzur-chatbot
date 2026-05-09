from __future__ import annotations

from dataclasses import dataclass
import re


_WORD_TO_NUMBER = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
}

_ASPECT_HINTS = {
    "portrait": "3:4",
    "vertical": "3:4",
    "landscape": "16:9",
    "wide": "16:9",
    "square": "1:1",
}

_IMAGE_TRIGGER_PATTERNS = [
    re.compile(r"^\s*/imagine\b", re.IGNORECASE),
    re.compile(r"\b(generate|create|make|draw|design|render)\b.{0,24}\b(image|picture|photo|art|illustration)\b", re.IGNORECASE),
    re.compile(r"\b(image|picture|photo|illustration)\b.{0,18}\b(of|for|showing|with)\b", re.IGNORECASE),
]

_EDIT_FOLLOWUP_PATTERN = re.compile(
    r"\b(make it|change it|edit it|regenerate|variation|more\s+|less\s+|add\s+|remove\s+)\b",
    re.IGNORECASE,
)


@dataclass
class ImageIntent:
    triggered: bool
    prompt: str = ""
    num_images: int = 1
    aspect_ratio: str | None = None
    negative_prompt: str | None = None


class ImageIntentService:
    @staticmethod
    def detect(
        message: str,
        recent_generated_prompt: str | None = None,
        default_aspect_ratio: str = "1:1",
    ) -> ImageIntent:
        text = (message or "").strip()
        if not text:
            return ImageIntent(triggered=False)

        is_explicit = any(pattern.search(text) for pattern in _IMAGE_TRIGGER_PATTERNS)
        is_followup_edit = bool(recent_generated_prompt and _EDIT_FOLLOWUP_PATTERN.search(text))

        if not is_explicit and not is_followup_edit:
            return ImageIntent(triggered=False)

        num_images = ImageIntentService._extract_num_images(text)
        aspect_ratio = ImageIntentService._extract_aspect_ratio(text) or default_aspect_ratio
        negative_prompt = ImageIntentService._extract_negative_prompt(text)

        cleaned_prompt = ImageIntentService._clean_prompt_text(text)
        if is_followup_edit and recent_generated_prompt:
            if cleaned_prompt:
                final_prompt = f"{recent_generated_prompt}. Apply this modification: {cleaned_prompt}"
            else:
                final_prompt = recent_generated_prompt
        else:
            final_prompt = cleaned_prompt or text

        return ImageIntent(
            triggered=True,
            prompt=final_prompt.strip(),
            num_images=num_images,
            aspect_ratio=aspect_ratio,
            negative_prompt=negative_prompt,
        )

    @staticmethod
    def _extract_num_images(text: str) -> int:
        numeric_match = re.search(r"(?:--n(?:um)?=|\b)([1-4])\s*(?:images?|pics?|pictures?)?\b", text, re.IGNORECASE)
        if numeric_match:
            return int(numeric_match.group(1))

        for word, value in _WORD_TO_NUMBER.items():
            if re.search(rf"\b{word}\s+(?:images?|pics?|pictures?)\b", text, re.IGNORECASE):
                return value

        return 1

    @staticmethod
    def _extract_aspect_ratio(text: str) -> str | None:
        ratio_match = re.search(r"--aspect(?:_ratio)?=([0-9]+:[0-9]+)", text, re.IGNORECASE)
        if ratio_match:
            return ratio_match.group(1)

        for hint, ratio in _ASPECT_HINTS.items():
            if re.search(rf"\b{hint}\b", text, re.IGNORECASE):
                return ratio

        return None

    @staticmethod
    def _extract_negative_prompt(text: str) -> str | None:
        negative_match = re.search(r"--negative=(.+)$", text, re.IGNORECASE)
        if not negative_match:
            return None

        value = negative_match.group(1).strip()
        return value if value else None

    @staticmethod
    def _clean_prompt_text(text: str) -> str:
        cleaned = text
        cleaned = re.sub(r"^\s*/imagine\b", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"--n(?:um)?=[1-4]", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"--aspect(?:_ratio)?=[0-9]+:[0-9]+", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"--negative=.+$", "", cleaned, flags=re.IGNORECASE)

        cleaned = re.sub(
            r"\b(generate|create|make|draw|design|render)\s+(an?\s+)?(image|picture|photo|art|illustration)\b",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )

        cleaned = re.sub(r"\s+", " ", cleaned).strip(" :,-")
        return cleaned
