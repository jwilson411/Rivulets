"""Fixed-size text chunking for knowledge-base ingestion (#98's v1 scope:
fixed chunk size, no semantic/recursive splitting -- see the issue's own
"probably worth a deliberately narrow v1" note).
"""

_CHUNK_SIZE = 1000  # characters
_CHUNK_OVERLAP = 100


def chunk_text(
    text: str, chunk_size: int = _CHUNK_SIZE, overlap: int = _CHUNK_OVERLAP
) -> list[str]:
    """Splits text into overlapping fixed-size character chunks. Overlap
    keeps a sentence that straddles a chunk boundary retrievable from
    whichever chunk it mostly ends up in."""
    if chunk_size <= overlap:
        raise ValueError("chunk_size must be greater than overlap")

    stripped = text.strip()
    if not stripped:
        return []

    chunks: list[str] = []
    start = 0
    while start < len(stripped):
        end = start + chunk_size
        chunks.append(stripped[start:end])
        if end >= len(stripped):
            break
        start = end - overlap
    return chunks
