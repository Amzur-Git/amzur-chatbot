import asyncio
import json
import logging
import re
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm import client
from app.core.config import settings


logger = logging.getLogger(__name__)


@dataclass
class DbQaContext:
    allowed_tables: set[str]
    schema_description: str


class DbQaService:
    _WRITE_KEYWORDS = re.compile(
        r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|execute|call|merge|copy|vacuum|analyze|set|reset)\b",
        re.IGNORECASE,
    )
    _INTENT_PATTERN = re.compile(
        r"\b(database|db|table|tables|sql|rows|records|count|sum|average|avg|min|max|group by|where|join)\b",
        re.IGNORECASE,
    )
    _FROM_JOIN_PATTERN = re.compile(
        r'\b(?:from|join)\s+([a-zA-Z_][\w\."]*)',
        re.IGNORECASE,
    )
    _MESSAGES_TODAY_PATTERN = re.compile(
        r"\b(how many|count|number of)\b.*\b(message|messages)\b.*\b(today)\b",
        re.IGNORECASE,
    )
    _MESSAGES_BY_USER_PATTERN = re.compile(
        r"\b(count|how many|number of|total number of|find the total number of)\b.*\b(messages?)\b.*\b(sent by|by)\b\s+(?P<name>.+?)(?:\s+using\b|\s+where\b|\s*$)",
        re.IGNORECASE,
    )
    _QUOTED_NAME_PATTERN = re.compile(r"['\"](?P<name>[^'\"]+)['\"]")

    @staticmethod
    def _split_sql_statements(sql: str) -> list[str]:
        statements: list[str] = []
        buffer: list[str] = []

        in_single = False
        in_double = False
        in_line_comment = False
        in_block_comment = False

        i = 0
        while i < len(sql):
            ch = sql[i]
            nxt = sql[i + 1] if i + 1 < len(sql) else ""

            if in_line_comment:
                buffer.append(ch)
                if ch == "\n":
                    in_line_comment = False
                i += 1
                continue

            if in_block_comment:
                buffer.append(ch)
                if ch == "*" and nxt == "/":
                    buffer.append(nxt)
                    in_block_comment = False
                    i += 2
                    continue
                i += 1
                continue

            if not in_single and not in_double:
                if ch == "-" and nxt == "-":
                    buffer.append(ch)
                    buffer.append(nxt)
                    in_line_comment = True
                    i += 2
                    continue
                if ch == "/" and nxt == "*":
                    buffer.append(ch)
                    buffer.append(nxt)
                    in_block_comment = True
                    i += 2
                    continue

            if ch == "'" and not in_double:
                # Handle escaped quote in string literal: ''
                if in_single and nxt == "'":
                    buffer.append(ch)
                    buffer.append(nxt)
                    i += 2
                    continue
                in_single = not in_single
                buffer.append(ch)
                i += 1
                continue

            if ch == '"' and not in_single:
                in_double = not in_double
                buffer.append(ch)
                i += 1
                continue

            if ch == ";" and not in_single and not in_double:
                statement = "".join(buffer).strip()
                if statement:
                    statements.append(statement)
                buffer = []
                i += 1
                continue

            buffer.append(ch)
            i += 1

        tail = "".join(buffer).strip()
        if tail:
            statements.append(tail)

        return statements

    @staticmethod
    def _extract_first_read_only_statement(sql: str) -> str:
        cleaned = re.sub(r"^```(?:sql)?\s*|\s*```$", "", sql.strip(), flags=re.IGNORECASE | re.DOTALL).strip()

        # Model output can include prose before the query; trim to first SELECT/WITH.
        match = re.search(r"\b(select|with)\b", cleaned, flags=re.IGNORECASE)
        if match:
            cleaned = cleaned[match.start():]

        statements = DbQaService._split_sql_statements(cleaned)
        if not statements:
            return ""

        for statement in statements:
            if re.match(r"^(select|with)\b", statement, flags=re.IGNORECASE):
                return statement.strip()

        return ""

    @staticmethod
    def _split_csv(raw_value: str) -> list[str]:
        if not raw_value:
            return []
        return [item.strip() for item in raw_value.split(",") if item.strip()]

    @staticmethod
    def _normalize_table_name(name: str) -> str:
        clean = name.strip().strip('"')
        if "." in clean:
            clean = clean.split(".")[-1]
        return clean.lower()

    @staticmethod
    def should_handle(message: str, db_query_mode: bool) -> bool:
        if not settings.DB_QA_ENABLED:
            return False

        msg = (message or "").strip()
        if not msg:
            return False

        if db_query_mode:
            return True

        # Safety net: handle a small set of deterministic read-only count intents
        # even when the UI forgets to set db_query_mode.
        if DbQaService._MESSAGES_TODAY_PATTERN.search(msg):
            return True
        if DbQaService._MESSAGES_BY_USER_PATTERN.search(msg):
            return True

        return False

    @staticmethod
    async def _build_context(db: AsyncSession) -> DbQaContext:
        allowed_schemas = DbQaService._split_csv(settings.DB_QA_ALLOWED_SCHEMAS) or ["public"]
        allowed_table_config = {
            DbQaService._normalize_table_name(item)
            for item in DbQaService._split_csv(settings.DB_QA_ALLOWED_TABLES)
        }
        blocked_tables = {
            DbQaService._normalize_table_name(item)
            for item in DbQaService._split_csv(settings.DB_QA_BLOCKED_TABLES)
        }

        result = await db.execute(
            text(
                """
                SELECT table_schema, table_name, column_name, data_type, ordinal_position
                FROM information_schema.columns
                WHERE table_schema = ANY(:schemas)
                ORDER BY table_schema, table_name, ordinal_position
                """
            ),
            {"schemas": allowed_schemas},
        )
        rows = result.mappings().all()

        if not rows:
            # Some managed Postgres setups can restrict information_schema visibility.
            # Fall back to pg_catalog to discover user-table columns.
            fallback_result = await db.execute(
                text(
                    """
                    SELECT
                        n.nspname AS table_schema,
                        c.relname AS table_name,
                        a.attname AS column_name,
                        pg_catalog.format_type(a.atttypid, a.atttypmod) AS data_type,
                        a.attnum AS ordinal_position
                    FROM pg_catalog.pg_namespace n
                    JOIN pg_catalog.pg_class c ON c.relnamespace = n.oid
                    JOIN pg_catalog.pg_attribute a ON a.attrelid = c.oid
                    WHERE n.nspname = ANY(:schemas)
                      AND c.relkind = 'r'
                      AND a.attnum > 0
                      AND NOT a.attisdropped
                    ORDER BY n.nspname, c.relname, a.attnum
                    """
                ),
                {"schemas": allowed_schemas},
            )
            rows = fallback_result.mappings().all()

        table_to_columns: dict[str, list[tuple[str, str]]] = {}
        table_to_columns_blocked_only: dict[str, list[tuple[str, str]]] = {}
        for row in rows:
            table_name = DbQaService._normalize_table_name(row["table_name"])
            if table_name in blocked_tables:
                continue

            schema_name = (row["table_schema"] or "").strip()
            full_name = f"{schema_name}.{table_name}" if schema_name else table_name
            table_to_columns_blocked_only.setdefault(full_name, []).append((row["column_name"], row["data_type"]))

            if allowed_table_config and table_name not in allowed_table_config:
                continue
            table_to_columns.setdefault(full_name, []).append((row["column_name"], row["data_type"]))

        if not table_to_columns:
            if allowed_table_config and table_to_columns_blocked_only and settings.ENVIRONMENT == "development":
                # Development fallback: avoid blocking local testing because of stale/mismatched allowlists.
                logger.warning(
                    "DB_QA_ALLOWED_TABLES filter produced no matches; falling back to blocked-only table set in development. "
                    "allowed=%s blocked=%s available=%s",
                    sorted(allowed_table_config),
                    sorted(blocked_tables),
                    sorted(name.split(".")[-1].lower() for name in table_to_columns_blocked_only.keys()),
                )
                table_to_columns = table_to_columns_blocked_only

        if not table_to_columns:
            available_after_block = sorted(
                name.split(".")[-1].lower() for name in table_to_columns_blocked_only.keys()
            )
            raise HTTPException(
                status_code=400,
                detail=(
                    "Database Q&A (v2) is enabled but no tables are available after allow/block rules. "
                    "Set DB_QA_ALLOWED_TABLES (comma-separated) to expose safe tables. "
                    f"Effective config -> schemas={sorted(set(allowed_schemas))}, "
                    f"allowed={sorted(allowed_table_config)}, blocked={sorted(blocked_tables)}, "
                    f"available_after_block={available_after_block}"
                ),
            )

        allowed_tables = {name.split(".")[-1].lower() for name in table_to_columns.keys()}
        max_columns = max(1, settings.DB_QA_MAX_COLUMNS_PER_TABLE)

        lines: list[str] = []
        for table_name in sorted(table_to_columns.keys()):
            cols = table_to_columns[table_name][:max_columns]
            col_desc = ", ".join(f"{name} ({dtype})" for name, dtype in cols)
            lines.append(f"- {table_name}: {col_desc}")

        return DbQaContext(allowed_tables=allowed_tables, schema_description="\n".join(lines))

    @staticmethod
    async def _generate_sql(question: str, context: DbQaContext) -> str:
        prompt = (
            "You convert natural language questions into PostgreSQL SQL.\n"
            "Rules:\n"
            "1) Generate exactly one read-only query (SELECT or WITH ... SELECT).\n"
            "2) Only use tables from schema list below.\n"
            "3) Never use write operations.\n"
            "4) Prefer explicit column names when practical.\n"
            "5) Return STRICT JSON only: {\"sql\":\"...\"}.\n\n"
            f"Schema:\n{context.schema_description}\n\n"
            f"Question: {question.strip()}"
        )

        def _call_model() -> str:
            response = client.chat.completions.create(
                model=settings.LLM_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": "You are a precise SQL generator for PostgreSQL. Output valid JSON only.",
                    },
                    {"role": "user", "content": prompt},
                ],
                extra_body={"metadata": {"application": settings.APP_NAME, "purpose": "db-qa"}},
            )
            return (response.choices[0].message.content or "").strip()

        raw = await asyncio.to_thread(_call_model)
        try:
            payload = json.loads(raw)
            sql = str(payload.get("sql", "")).strip()
        except Exception:
            # Fallback when model emits plain text code fence.
            sql = re.sub(r"^```(?:sql)?\s*|\s*```$", "", raw, flags=re.IGNORECASE | re.DOTALL).strip()

        if not sql:
            raise HTTPException(status_code=400, detail="Unable to generate SQL for this question")
        return sql

    @staticmethod
    def _extract_referenced_tables(sql: str) -> set[str]:
        found = set()
        for match in DbQaService._FROM_JOIN_PATTERN.findall(sql):
            token = match.split()[0].strip().strip(",")
            found.add(DbQaService._normalize_table_name(token))
        return found

    @staticmethod
    def _validate_sql(sql: str, context: DbQaContext) -> str:
        candidate = DbQaService._extract_first_read_only_statement(sql).strip().rstrip(";").strip()
        if not candidate:
            raise HTTPException(status_code=400, detail="Generated SQL is empty")

        if not re.match(r"^(select|with)\b", candidate, flags=re.IGNORECASE):
            raise HTTPException(status_code=400, detail="Only read-only SELECT queries are allowed")

        if DbQaService._WRITE_KEYWORDS.search(candidate):
            raise HTTPException(status_code=400, detail="Unsafe SQL keyword detected")

        referenced = DbQaService._extract_referenced_tables(candidate)
        disallowed = sorted(table for table in referenced if table not in context.allowed_tables)
        if disallowed:
            raise HTTPException(
                status_code=400,
                detail=f"Query references disallowed tables: {', '.join(disallowed)}",
            )

        return candidate

    @staticmethod
    def _extract_user_name(candidate: str) -> str:
        text_candidate = (candidate or "").strip()
        if not text_candidate:
            return ""

        quoted = DbQaService._QUOTED_NAME_PATTERN.search(text_candidate)
        if quoted:
            return (quoted.group("name") or "").strip()

        normalized = re.sub(r"\b(user\s+with\s+name\s+as|name\s+as|name\s+is|named)\b", "", text_candidate, flags=re.IGNORECASE)
        normalized = normalized.strip().strip('"').strip("'")
        return normalized

    @staticmethod
    def _try_builtin_sql(question: str, context: DbQaContext) -> str | None:
        q = (question or "").strip()
        if not q:
            return None

        if "messages" in context.allowed_tables and DbQaService._MESSAGES_TODAY_PATTERN.search(q):
            return "SELECT COUNT(id) AS new_messages_count FROM public.messages WHERE DATE(created_at) = CURRENT_DATE"

        if {"messages", "users"}.issubset(context.allowed_tables):
            match = DbQaService._MESSAGES_BY_USER_PATTERN.search(q)
            if match:
                raw_name = DbQaService._extract_user_name(match.group("name") or "")
                if raw_name:
                    # Escape single quotes for safe SQL string literal embedding.
                    name_literal = raw_name.replace("'", "''")
                    return (
                        "SELECT COUNT(m.id) AS total_messages_sent "
                        "FROM public.messages AS m "
                        "JOIN public.users AS u ON m.user_id = u.id "
                        "WHERE m.role = 'user' "
                        f"AND lower(trim(coalesce(u.full_name, ''))) = lower(trim('{name_literal}'))"
                    )

        return None

    @staticmethod
    def _format_markdown_table(rows: list[dict], max_display_rows: int = 20) -> str:
        if not rows:
            return "No rows returned."

        headers = list(rows[0].keys())
        header_row = "| " + " | ".join(headers) + " |"
        separator = "| " + " | ".join("---" for _ in headers) + " |"

        body_rows = []
        for row in rows[:max_display_rows]:
            values = []
            for key in headers:
                value = row.get(key)
                text_value = "" if value is None else str(value)
                values.append(text_value.replace("\n", " ")[:160])
            body_rows.append("| " + " | ".join(values) + " |")

        output = "\n".join([header_row, separator, *body_rows])
        if len(rows) > max_display_rows:
            output += f"\n\nShowing first {max_display_rows} of {len(rows)} rows."
        return output

    @staticmethod
    async def answer_question(db: AsyncSession, message: str, db_query_mode: bool = False) -> str | None:
        if not DbQaService.should_handle(message, db_query_mode):
            return None

        question = (message or "").strip()
        if not question:
            raise HTTPException(status_code=400, detail="Please provide a database question")

        context = await DbQaService._build_context(db)
        builtin_sql = DbQaService._try_builtin_sql(question, context)
        if builtin_sql:
            safe_sql = builtin_sql
        else:
            generated_sql = await DbQaService._generate_sql(question, context)
            safe_sql = DbQaService._validate_sql(generated_sql, context)

        wrapped_sql = f"SELECT * FROM ({safe_sql}) AS dbqa_subquery LIMIT :max_rows"
        max_rows = max(1, settings.DB_QA_MAX_ROWS)

        try:
            result = await db.execute(text(wrapped_sql), {"max_rows": max_rows})
            rows = [dict(item) for item in result.mappings().all()]
        except HTTPException:
            raise
        except Exception as error:
            logger.exception("DB Q&A execution failed")
            raise HTTPException(status_code=400, detail=f"Failed to execute generated SQL: {error}")

        table = DbQaService._format_markdown_table(rows)
        return (
            "Database answer (read-only):\n\n"
            f"SQL used:\n```sql\n{safe_sql}\n```\n\n"
            f"{table}"
        )
