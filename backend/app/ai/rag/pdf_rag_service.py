from __future__ import annotations

from pathlib import Path
import uuid

from fastapi import HTTPException
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pypdf import PdfReader

from app.ai.llm import embeddings
from app.core.config import settings


class PdfRagService:
    COLLECTION_NAME = "pdf_attachments"
    _vectorstore: Chroma | None = None

    @classmethod
    def _get_vectorstore(cls) -> Chroma:
        if cls._vectorstore is not None:
            return cls._vectorstore

        persist_dir = Path(settings.CHROMA_PERSIST_DIR)
        persist_dir.mkdir(parents=True, exist_ok=True)

        cls._vectorstore = Chroma(
            collection_name=cls.COLLECTION_NAME,
            persist_directory=str(persist_dir),
            embedding_function=embeddings,
        )
        return cls._vectorstore

    @staticmethod
    def _extract_pdf_pages(file_path: str) -> list[tuple[int, str]]:
        path = Path(file_path)
        if not path.exists():
            raise HTTPException(status_code=404, detail="PDF file not found")

        try:
            reader = PdfReader(str(path))
        except Exception as error:
            raise HTTPException(status_code=400, detail=f"Unable to read PDF: {error}") from error

        pages: list[tuple[int, str]] = []
        for page_index, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            if text:
                pages.append((page_index, text))
        return pages

    @classmethod
    def _build_documents(
        cls,
        *,
        user_id: uuid.UUID,
        thread_id: uuid.UUID,
        attachment_id: uuid.UUID,
        file_name: str,
        pages: list[tuple[int, str]],
    ) -> list[Document]:
        raw_documents = [
            Document(
                page_content=text,
                metadata={
                    "user_id": str(user_id),
                    "thread_id": str(thread_id),
                    "attachment_id": str(attachment_id),
                    "file_name": file_name,
                    "page": page_number,
                },
            )
            for page_number, text in pages
        ]

        splitter = RecursiveCharacterTextSplitter(
            chunk_size=settings.PDF_RAG_CHUNK_SIZE,
            chunk_overlap=settings.PDF_RAG_CHUNK_OVERLAP,
            separators=["\n\n", "\n", ". ", " ", ""],
        )
        chunks = splitter.split_documents(raw_documents)

        for index, chunk in enumerate(chunks):
            chunk.metadata["chunk_index"] = index

        return chunks

    @classmethod
    def index_attachment(
        cls,
        *,
        user_id: uuid.UUID,
        thread_id: uuid.UUID,
        attachment_id: uuid.UUID,
        file_name: str,
        file_path: str,
    ) -> dict[str, int]:
        pages = cls._extract_pdf_pages(file_path)
        if not pages:
            raise HTTPException(status_code=400, detail="No extractable text found in PDF")

        documents = cls._build_documents(
            user_id=user_id,
            thread_id=thread_id,
            attachment_id=attachment_id,
            file_name=file_name,
            pages=pages,
        )

        vectorstore = cls._get_vectorstore()
        vectorstore.delete(where={"attachment_id": str(attachment_id)})

        ids = [f"{attachment_id}:{index}" for index in range(len(documents))]
        vectorstore.add_documents(documents=documents, ids=ids)

        return {
            "rag_pages_indexed": len(pages),
            "rag_chunks_indexed": len(documents),
        }

    @classmethod
    def delete_attachment(cls, attachment_id: uuid.UUID) -> None:
        vectorstore = cls._get_vectorstore()
        vectorstore.delete(where={"attachment_id": str(attachment_id)})

    @classmethod
    def search(
        cls,
        *,
        user_id: uuid.UUID,
        thread_id: uuid.UUID,
        query: str,
        top_k: int,
    ) -> list[Document]:
        vectorstore = cls._get_vectorstore()
        return vectorstore.similarity_search(
            query,
            k=max(1, top_k),
            filter={
                "user_id": str(user_id),
                "thread_id": str(thread_id),
            },
        )

    @classmethod
    def build_context(
        cls,
        *,
        user_id: uuid.UUID,
        thread_id: uuid.UUID,
        query: str,
        top_k: int,
        max_chars: int,
    ) -> str:
        docs = cls.search(user_id=user_id, thread_id=thread_id, query=query, top_k=top_k)
        if not docs:
            return ""

        lines: list[str] = ["PDF retrieval context:"]
        for index, doc in enumerate(docs, start=1):
            file_name = doc.metadata.get("file_name", "document")
            page = doc.metadata.get("page", "?")
            snippet = doc.page_content.strip().replace("\n", " ")
            lines.append(f"{index}. {file_name} (page {page})")
            lines.append(f"   {snippet}")

        return "\n".join(lines)[:max_chars]
