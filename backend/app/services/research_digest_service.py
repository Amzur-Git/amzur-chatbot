from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterator

from app.ai.llm import llm


_JSON_BLOCK_PATTERN = re.compile(r"\{[\s\S]*\}")


@dataclass
class PaperRecord:
    title: str
    summary: str
    authors: str
    published: str
    url: str
    entry_id: str


class ResearchDigestService:
    """Autonomous arXiv researcher that streams status + digest chunks as SSE events."""

    @classmethod
    def stream_digest(
        cls,
        *,
        topic: str,
        max_rounds: int,
        papers_per_round: int,
        min_papers: int,
    ) -> Iterator[str]:
        topic = topic.strip()
        started_at = datetime.utcnow().isoformat() + "Z"

        yield cls._sse(
            "status",
            {
                "message": f"Starting research for '{topic}'",
                "topic": topic,
                "started_at": started_at,
            },
        )

        papers: list[PaperRecord] = []
        seen_ids: set[str] = set()
        rounds_used = 0

        for round_index in range(max_rounds):
            rounds_used = round_index + 1
            target_limit = (round_index + 1) * papers_per_round

            yield cls._sse(
                "status",
                {
                    "message": f"Searching arXiv (round {rounds_used}/{max_rounds})",
                    "round": rounds_used,
                    "target_limit": target_limit,
                },
            )

            try:
                fetched = cls._search_arxiv(topic=topic, max_results=target_limit)
            except Exception as exc:
                yield cls._sse(
                    "error",
                    {
                        "message": "Search failed while querying arXiv.",
                        "detail": str(exc),
                        "round": rounds_used,
                    },
                )
                break
            new_batch: list[PaperRecord] = []
            for record in fetched:
                if record.entry_id in seen_ids:
                    continue
                seen_ids.add(record.entry_id)
                papers.append(record)
                new_batch.append(record)

            yield cls._sse(
                "papers",
                {
                    "round": rounds_used,
                    "new_count": len(new_batch),
                    "total_count": len(papers),
                    "papers": [cls._paper_to_payload(item) for item in new_batch],
                },
            )

            if not new_batch:
                yield cls._sse(
                    "status",
                    {
                        "message": "No new papers found in this round. Proceeding to synthesis.",
                        "round": rounds_used,
                    },
                )
                break

            decision = cls._decide_enough_evidence(
                topic=topic,
                papers=papers,
                round_index=round_index,
                max_rounds=max_rounds,
                min_papers=min_papers,
            )
            yield cls._sse("decision", decision)

            if decision.get("enough"):
                break

        if not papers:
            yield cls._sse(
                "error",
                {
                    "message": "Could not find relevant papers on arXiv for this topic.",
                },
            )
            yield cls._sse("done", {"ok": False, "topic": topic})
            return

        yield cls._sse(
            "status",
            {
                "message": f"Synthesizing digest from {len(papers)} papers...",
                "total_papers": len(papers),
                "rounds_used": rounds_used,
            },
        )

        digest_chunks: list[str] = []
        try:
            for chunk in cls._stream_digest_from_llm(topic=topic, papers=papers):
                if not chunk:
                    continue
                digest_chunks.append(chunk)
                yield cls._sse("digest_chunk", {"chunk": chunk})
            digest_text = "".join(digest_chunks).strip()
        except Exception:
            digest_text = cls._build_fallback_digest(topic=topic, papers=papers)
            yield cls._sse("digest_chunk", {"chunk": digest_text})

        yield cls._sse(
            "final_digest",
            {
                "topic": topic,
                "digest": digest_text,
                "papers": [cls._paper_to_payload(item) for item in papers],
                "rounds_used": rounds_used,
            },
        )
        yield cls._sse("done", {"ok": True, "topic": topic})

    @staticmethod
    def _sse(event: str, data: dict[str, Any]) -> str:
        return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    @classmethod
    def _search_arxiv(cls, *, topic: str, max_results: int) -> list[PaperRecord]:
        from mcp_simple_arxiv.arxiv_client import ArxivClient, SortBy, SortOrder

        client = ArxivClient()
        search_result = cls._run_async(
            client.search(
                query=topic,
                max_results=max_results,
                sort_by=SortBy.RELEVANCE,
                sort_order=SortOrder.DESCENDING,
            )
        )

        records: list[PaperRecord] = []
        for paper in search_result.papers:
            authors_raw = paper.get("authors")
            if isinstance(authors_raw, list):
                authors = ", ".join(str(name) for name in authors_raw[:6])
            else:
                authors = ""

            paper_id = str(paper.get("id") or "").strip()
            entry_id = f"https://arxiv.org/abs/{paper_id}" if paper_id else ""
            url = (
                str(paper.get("pdf_url") or "").strip()
                or str(paper.get("abstract_url") or "").strip()
                or str(paper.get("html_url") or "").strip()
                or entry_id
            )

            records.append(
                PaperRecord(
                    title=str(paper.get("title") or "").strip(),
                    summary=" ".join(str(paper.get("summary") or "").split()),
                    authors=authors,
                    published=cls._normalize_published(str(paper.get("published") or "")),
                    url=url,
                    entry_id=entry_id or url,
                )
            )
        return records

    @staticmethod
    def _run_async(coro: Any) -> Any:
        # Keep this service synchronous while calling async MCP-backed client code.
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    @staticmethod
    def _normalize_published(value: str) -> str:
        if not value:
            return "Unknown"
        text = value.strip()
        if not text:
            return "Unknown"
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).strftime("%Y-%m-%d")
        except ValueError:
            return text.split("T", 1)[0] if "T" in text else text

    @classmethod
    def _decide_enough_evidence(
        cls,
        *,
        topic: str,
        papers: list[PaperRecord],
        round_index: int,
        max_rounds: int,
        min_papers: int,
    ) -> dict[str, Any]:
        # If we are in the last round, force synthesis.
        if round_index >= max_rounds - 1:
            return {
                "enough": True,
                "reason": "Reached max rounds; proceeding with available evidence.",
                "confidence": 0.7,
                "missing_aspects": [],
            }

        evidence_lines = []
        for idx, paper in enumerate(papers[:12], start=1):
            evidence_lines.append(
                f"{idx}. {paper.title} ({paper.published}) | {paper.authors} | {paper.summary[:220]}"
            )
        evidence_text = "\n".join(evidence_lines)

        prompt = (
            "You are deciding whether we have enough evidence to write a high-quality research digest.\n"
            f"Topic: {topic}\n"
            f"Current paper count: {len(papers)}\n"
            f"Minimum desired paper count: {min_papers}\n"
            "Evidence:\n"
            f"{evidence_text}\n\n"
            "Return strict JSON with keys: enough (boolean), reason (string), confidence (0..1), missing_aspects (array of strings)."
        )

        try:
            response = llm.invoke(prompt)
            parsed = cls._parse_json_decision(cls._response_text(response))
        except Exception:
            parsed = None

        if not parsed:
            return {
                "enough": len(papers) >= min_papers and round_index >= 1,
                "reason": "Fallback heuristic used because the decision parser could not read model output.",
                "confidence": 0.55,
                "missing_aspects": [],
            }

        enough = bool(parsed.get("enough"))
        if len(papers) < min_papers:
            enough = False

        confidence = parsed.get("confidence", 0.6)
        if not isinstance(confidence, (int, float)):
            confidence = 0.6

        missing_aspects = parsed.get("missing_aspects")
        if not isinstance(missing_aspects, list):
            missing_aspects = []

        reason = parsed.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            reason = "Decision generated from model output."

        return {
            "enough": enough,
            "reason": reason.strip(),
            "confidence": float(max(0.0, min(1.0, confidence))),
            "missing_aspects": [str(item) for item in missing_aspects][:6],
        }

    @classmethod
    def _stream_digest_from_llm(cls, *, topic: str, papers: list[PaperRecord]) -> Iterator[str]:
        evidence_lines = []
        for idx, paper in enumerate(papers[:20], start=1):
            evidence_lines.append(
                f"[{idx}] Title: {paper.title}\n"
                f"Authors: {paper.authors}\n"
                f"Published: {paper.published}\n"
                f"Summary: {paper.summary}\n"
                f"URL: {paper.url}"
            )

        prompt = (
            "Create a structured, evidence-grounded research digest in markdown.\n"
            f"Topic: {topic}\n\n"
            "Requirements:\n"
            "- Use headings exactly: Executive Summary, Key Findings, Methods Landscape, Consensus and Disagreement, Open Questions, Practical Takeaways, References.\n"
            "- Cite references inline using [n] where n maps to the evidence list index.\n"
            "- Be concise but specific.\n"
            "- Include a markdown table in Key Findings with columns: Finding, Evidence, Confidence.\n"
            "- In References, include bullet list with paper title and URL.\n\n"
            "Evidence:\n"
            f"{chr(10).join(evidence_lines)}"
        )

        for chunk in llm.stream(prompt):
            text = cls._response_text(chunk)
            if text:
                yield text

    @staticmethod
    def _response_text(response: Any) -> str:
        content = getattr(response, "content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str):
                        parts.append(text)
            return "".join(parts)
        return str(content or "")

    @classmethod
    def _parse_json_decision(cls, text: str) -> dict[str, Any] | None:
        if not text:
            return None

        candidate = text.strip()
        if candidate.startswith("```"):
            candidate = candidate.strip("`")
            candidate = candidate.replace("json", "", 1).strip()

        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

        match = _JSON_BLOCK_PATTERN.search(text)
        if not match:
            return None

        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None

        return parsed if isinstance(parsed, dict) else None

    @classmethod
    def _paper_to_payload(cls, item: PaperRecord) -> dict[str, Any]:
        return {
            "title": item.title,
            "summary": item.summary,
            "authors": item.authors,
            "published": item.published,
            "url": item.url,
            "entry_id": item.entry_id,
        }

    @classmethod
    def _build_fallback_digest(cls, *, topic: str, papers: list[PaperRecord]) -> str:
        top_papers = papers[:8]
        lines = [
            f"## Executive Summary\n",
            f"A digest for **{topic}** was generated from {len(papers)} arXiv papers.\n",
            "\n## Key Findings\n",
        ]

        for idx, paper in enumerate(top_papers, start=1):
            lines.append(f"- [{idx}] **{paper.title}** ({paper.published}) by {paper.authors}.\n")

        lines.append("\n## References\n")
        for idx, paper in enumerate(top_papers, start=1):
            lines.append(f"- [{idx}] [{paper.title}]({paper.url})\n")

        return "".join(lines)
