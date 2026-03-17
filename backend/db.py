from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

DB_PATH = Path(__file__).resolve().parent / "observatory.db"


def _connect() -> aiosqlite.Connection:
    return aiosqlite.connect(DB_PATH)


async def init_db() -> None:
    async with _connect() as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS query_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                disease TEXT NOT NULL,
                region TEXT NOT NULL,
                confidence REAL NOT NULL,
                source_ok_count INTEGER NOT NULL,
                source_total_count INTEGER NOT NULL,
                generated_at_utc TEXT NOT NULL
            )
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_query_history_generated_at
            ON query_history(generated_at_utc DESC)
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS malacards_diseases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                source_file TEXT,
                updated_at_utc TEXT NOT NULL
            )
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_malacards_name
            ON malacards_diseases(name)
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS alert_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                source TEXT NOT NULL,
                alert TEXT NOT NULL,
                severity TEXT NOT NULL,
                created_at_utc TEXT NOT NULL,
                UNIQUE(date, source, alert)
            )
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_alert_events_date
            ON alert_events(date DESC)
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS disease_profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                disease TEXT NOT NULL UNIQUE,
                profile_json TEXT NOT NULL,
                source_ok_count INTEGER NOT NULL,
                updated_at_utc TEXT NOT NULL
            )
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_disease_profiles_updated
            ON disease_profiles(updated_at_utc DESC)
            """
        )
        await conn.commit()


async def save_query(
    disease: str,
    region: str,
    confidence: float,
    source_ok_count: int,
    source_total_count: int,
) -> None:
    async with _connect() as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute(
            """
            INSERT INTO query_history(disease, region, confidence, source_ok_count, source_total_count, generated_at_utc)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                disease,
                region,
                confidence,
                source_ok_count,
                source_total_count,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        await conn.commit()


async def list_history(limit: int = 25) -> list[dict]:
    async with _connect() as conn:
        conn.row_factory = aiosqlite.Row
        cursor = await conn.execute(
            """
            SELECT id, disease, region, confidence, source_ok_count, source_total_count, generated_at_utc
            FROM query_history
            ORDER BY generated_at_utc DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def get_usage_stats() -> dict:
    async with _connect() as conn:
        conn.row_factory = aiosqlite.Row
        cur_total = await conn.execute("SELECT COUNT(1) AS c FROM query_history")
        total_row = await cur_total.fetchone()
        total_queries = total_row["c"] if total_row else 0

        cur_disease = await conn.execute(
            """
            SELECT disease, COUNT(1) AS count
            FROM query_history
            GROUP BY disease
            ORDER BY count DESC
            LIMIT 5
            """
        )
        by_disease = await cur_disease.fetchall()

        cur_region = await conn.execute(
            """
            SELECT region, COUNT(1) AS count
            FROM query_history
            GROUP BY region
            ORDER BY count DESC
            LIMIT 5
            """
        )
        by_region = await cur_region.fetchall()

    return {
        "total_queries": total_queries,
        "top_diseases": [dict(row) for row in by_disease],
        "top_regions": [dict(row) for row in by_region],
    }


async def upsert_malacards_names(names: list[str], source_file: str = "") -> int:
    clean_names = sorted({name.strip() for name in names if name and name.strip()})
    if not clean_names:
        return 0

    now = datetime.now(timezone.utc).isoformat()
    async with _connect() as conn:
        conn.row_factory = aiosqlite.Row
        await conn.executemany(
            """
            INSERT INTO malacards_diseases(name, source_file, updated_at_utc)
            VALUES (?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                source_file=excluded.source_file,
                updated_at_utc=excluded.updated_at_utc
            """,
            [(name, source_file, now) for name in clean_names],
        )
        await conn.commit()
    return len(clean_names)


async def list_malacards_names(limit: int | None = None) -> list[str]:
    query = "SELECT name FROM malacards_diseases ORDER BY LOWER(name) ASC"
    params: tuple = ()
    if limit is not None:
        query += " LIMIT ?"
        params = (limit,)
    async with _connect() as conn:
        conn.row_factory = aiosqlite.Row
        cursor = await conn.execute(query, params)
        rows = await cursor.fetchall()
    return [row["name"] for row in rows]


async def malacards_stats() -> dict:
    async with _connect() as conn:
        conn.row_factory = aiosqlite.Row
        cursor = await conn.execute(
            """
            SELECT COUNT(1) AS count, MAX(updated_at_utc) AS last_updated_utc
            FROM malacards_diseases
            """
        )
        row = await cursor.fetchone()
    return {
        "count": row["count"] if row else 0,
        "last_updated_utc": row["last_updated_utc"] if row else None,
    }


async def upsert_alert_events(alerts: list[dict]) -> list[dict]:
    if not alerts:
        return []

    created = datetime.now(timezone.utc).isoformat()
    inserted: list[dict] = []
    async with _connect() as conn:
        conn.row_factory = aiosqlite.Row
        for alert in alerts:
            date = str(alert.get("date", "")).strip() or "unknown"
            source = str(alert.get("source", "")).strip() or "unknown"
            text = str(alert.get("alert", "")).strip()
            severity = str(alert.get("severity", "moderate")).strip() or "moderate"
            if not text:
                continue
            cursor = await conn.execute(
                """
                INSERT OR IGNORE INTO alert_events(date, source, alert, severity, created_at_utc)
                VALUES (?, ?, ?, ?, ?)
                """,
                (date, source, text, severity, created),
            )
            if cursor.rowcount and cursor.rowcount > 0:
                inserted.append({"date": date, "source": source, "alert": text, "severity": severity})
        await conn.commit()
    return inserted


async def list_alert_events(limit: int = 50, disease: str = "", region: str = "") -> list[dict]:
    conditions = []
    params: list[str | int] = []
    if disease.strip():
        conditions.append("LOWER(alert) LIKE ?")
        params.append(f"%{disease.strip().lower()}%")
    if region.strip():
        conditions.append("LOWER(alert) LIKE ?")
        params.append(f"%{region.strip().lower()}%")

    where_sql = f"WHERE {' OR '.join(conditions)}" if conditions else ""
    query = (
        "SELECT id, date, source, alert, severity, created_at_utc "
        f"FROM alert_events {where_sql} "
        "ORDER BY created_at_utc DESC LIMIT ?"
    )
    params.append(limit)

    async with _connect() as conn:
        conn.row_factory = aiosqlite.Row
        cursor = await conn.execute(query, tuple(params))
        rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def upsert_disease_profile(disease: str, profile: dict, source_ok_count: int) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    payload = json.dumps(profile, ensure_ascii=True)
    async with _connect() as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute(
            """
            INSERT INTO disease_profiles(disease, profile_json, source_ok_count, updated_at_utc)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(disease) DO UPDATE SET
                profile_json=excluded.profile_json,
                source_ok_count=excluded.source_ok_count,
                updated_at_utc=excluded.updated_at_utc
            """,
            (disease.strip(), payload, source_ok_count, now),
        )
        await conn.commit()
    return {"disease": disease.strip(), "source_ok_count": source_ok_count, "updated_at_utc": now}


async def get_disease_profile(disease: str) -> dict | None:
    async with _connect() as conn:
        conn.row_factory = aiosqlite.Row
        cursor = await conn.execute(
            """
            SELECT disease, profile_json, source_ok_count, updated_at_utc
            FROM disease_profiles
            WHERE LOWER(disease) = LOWER(?)
            LIMIT 1
            """,
            (disease.strip(),),
        )
        row = await cursor.fetchone()
    if not row:
        return None
    return {
        "disease": row["disease"],
        "profile": json.loads(row["profile_json"]),
        "source_ok_count": row["source_ok_count"],
        "updated_at_utc": row["updated_at_utc"],
    }


async def disease_profile_stats() -> dict:
    async with _connect() as conn:
        conn.row_factory = aiosqlite.Row
        cur_total = await conn.execute("SELECT COUNT(1) AS c FROM disease_profiles")
        total_row = await cur_total.fetchone()

        cur_recent = await conn.execute(
            """
            SELECT disease, source_ok_count, updated_at_utc
            FROM disease_profiles
            ORDER BY updated_at_utc DESC
            LIMIT 10
            """
        )
        recent_rows = await cur_recent.fetchall()

        cur_missing = await conn.execute(
            """
            SELECT COUNT(1) AS c
            FROM malacards_diseases m
            LEFT JOIN disease_profiles p ON LOWER(m.name)=LOWER(p.disease)
            WHERE p.id IS NULL
            """
        )
        missing_row = await cur_missing.fetchone()

    return {
        "profile_count": total_row["c"] if total_row else 0,
        "missing_malacards_profiles": missing_row["c"] if missing_row else 0,
        "recent": [dict(row) for row in recent_rows],
    }


async def list_malacards_without_profile(limit: int = 100, offset: int = 0) -> list[str]:
    async with _connect() as conn:
        conn.row_factory = aiosqlite.Row
        cursor = await conn.execute(
            """
            SELECT m.name
            FROM malacards_diseases m
            LEFT JOIN disease_profiles p ON LOWER(m.name)=LOWER(p.disease)
            WHERE p.id IS NULL
            ORDER BY LOWER(m.name) ASC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        )
        rows = await cursor.fetchall()
    return [row["name"] for row in rows]
