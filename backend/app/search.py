"""Meeting memory: index transcripts and summaries, then hybrid-search them (vector + full-text, fused with RRF)."""

import re
from uuid import UUID

from app.db import get_pool
from app.llm import embed

CHUNK_CHARS = 1200
OVERLAP_LINES = 2

HYBRID_SQL = """
WITH vec AS (
    SELECT id, row_number() OVER (ORDER BY embedding <=> %(qvec)s) AS rank
    FROM chunks
    WHERE owner_id = %(owner)s AND (%(exclude)s::uuid IS NULL OR meeting_id <> %(exclude)s::uuid)
    ORDER BY embedding <=> %(qvec)s
    LIMIT 30
),
kw AS (
    SELECT id, row_number() OVER (ORDER BY ts_rank_cd(tsv, query) DESC) AS rank
    FROM chunks, websearch_to_tsquery('english', %(kwq)s) AS query
    WHERE owner_id = %(owner)s AND tsv @@ query
      AND (%(exclude)s::uuid IS NULL OR meeting_id <> %(exclude)s::uuid)
    ORDER BY ts_rank_cd(tsv, query) DESC
    LIMIT 30
),
fused AS (
    SELECT id, sum(1.0 / (60 + rank)) AS score
    FROM (SELECT * FROM vec UNION ALL SELECT * FROM kw) AS both_lists
    GROUP BY id
)
SELECT c.meeting_id, m.title, m.meeting_date, c.kind, c.content, round(f.score::numeric, 5)::float AS score
FROM fused f
JOIN chunks c ON c.id = f.id
JOIN meetings m ON m.id = c.meeting_id
ORDER BY f.score DESC
LIMIT %(k)s
"""


def chunk_transcript(transcript: str) -> list[str]:
    """Groups whole lines (speaker turns) into ~1200-character chunks, repeating the last 2 lines as overlap."""
    lines = [ln for ln in transcript.splitlines() if ln.strip()]
    chunks: list[str] = []
    current: list[str] = []
    fresh = 0  # lines added since the last chunk (the overlap lines don't count)
    for line in lines:
        line = line[:CHUNK_CHARS]
        if fresh and sum(len(x) + 1 for x in current) + len(line) > CHUNK_CHARS:
            chunks.append("\n".join(current))
            current, fresh = current[-OVERLAP_LINES:], 0
        current.append(line)
        fresh += 1
    if fresh:
        chunks.append("\n".join(current))
    return chunks


async def index_meeting(owner_id: str, meeting_id: str, title: str, summary: str, transcript: str) -> int:
    """Replaces the meeting's chunks in one transaction, so re-running is safe."""
    texts = [f"{title}\nSummary: {summary}"] + chunk_transcript(transcript)
    kinds = ["summary"] + ["transcript"] * (len(texts) - 1)
    vectors = await embed(texts)
    async with get_pool().connection() as conn, conn.transaction():
        await conn.execute("DELETE FROM chunks WHERE meeting_id = %s", (meeting_id,))
        async with conn.cursor() as cur:
            await cur.executemany(
                "INSERT INTO chunks (owner_id, meeting_id, kind, content, embedding) VALUES (%s, %s, %s, %s, %s)",
                [(owner_id, meeting_id, k, t, v) for k, t, v in zip(kinds, texts, vectors, strict=True)],
            )
    return len(texts)


async def search_meetings(
    owner_id: str, query: str, k: int = 5, exclude_meeting: str | UUID | None = None
) -> list[dict]:
    [qvec] = await embed([query])
    async with get_pool().connection() as conn:
        cur = await conn.execute(
            HYBRID_SQL,
            {
                "qvec": qvec,
                "owner": owner_id,  # tenant isolation
                "kwq": " or ".join(re.findall(r"\w+", query)),
                "exclude": str(exclude_meeting) if exclude_meeting else None,
                "k": k,
            },
        )
        return await cur.fetchall()
