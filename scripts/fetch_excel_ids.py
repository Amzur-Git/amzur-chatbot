import argparse
import asyncio
import json
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


def read_database_url(env_path: Path) -> str:
    if not env_path.exists():
        raise FileNotFoundError(f".env file not found: {env_path}")

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")

    raise ValueError(f"DATABASE_URL not found in {env_path}")


async def fetch_ids(database_url: str, limit: int, user_email: str | None) -> list[dict]:
    engine = create_async_engine(database_url, future=True)
    base_select = """
        SELECT
            a.id::text AS file_id,
            a.thread_id::text AS chat_thread_id,
            a.file_name,
            a.created_at,
            u.email AS user_email
        FROM attachments a
        LEFT JOIN users u ON u.id = a.user_id
        WHERE a.file_type = 'table'
    """

    try:
        async with engine.connect() as conn:
            if user_email:
                query = text(
                    base_select
                    + """
                    AND lower(u.email) = lower(:user_email)
                    ORDER BY a.created_at DESC
                    LIMIT :limit
                    """
                )
                params = {"limit": limit, "user_email": user_email}
            else:
                query = text(
                    base_select
                    + """
                    ORDER BY a.created_at DESC
                    LIMIT :limit
                    """
                )
                params = {"limit": limit}

            rows = (await conn.execute(query, params)).mappings().all()
            return [dict(r) for r in rows]
    finally:
        await engine.dispose()


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch recent table file_id and chat_thread_id from backend DB"
    )
    parser.add_argument(
        "--env",
        default="backend/.env",
        help="Path to .env file containing DATABASE_URL (default: backend/.env)",
    )
    parser.add_argument("--limit", type=int, default=10, help="Number of rows to return")
    parser.add_argument(
        "--user-email",
        default=None,
        help="Optional filter for one user's email",
    )
    parser.add_argument(
        "--latest-only",
        action="store_true",
        help="Print only the newest file/thread pair",
    )
    parser.add_argument(
        "--one-line",
        action="store_true",
        help="Print newest pair in one line for quick copy/paste",
    )
    args = parser.parse_args()

    database_url = read_database_url(Path(args.env))
    rows = await fetch_ids(database_url, args.limit, args.user_email)

    if not rows:
        print("No table attachments found.")
        return

    first = rows[0]

    if args.one_line:
        print(f"file_id={first['file_id']} chat_thread_id={first['chat_thread_id']}")
        return

    if args.latest_only:
        print(json.dumps(first, indent=2, default=str))
        print("\nUse these in n8n Normalize Input:")
        print(f"file_id: {first['file_id']}")
        print(f"chat_thread_id: {first['chat_thread_id']}")
        return

    print("Recent table attachment IDs:\n")
    print(json.dumps(rows, indent=2, default=str))
    print("\nUse these in n8n Normalize Input:")
    print(f"file_id: {first['file_id']}")
    print(f"chat_thread_id: {first['chat_thread_id']}")


if __name__ == "__main__":
    asyncio.run(main())
