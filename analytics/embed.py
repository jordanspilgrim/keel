"""De-identified embedding (plan §3d, §5, Phase 4).

Embeds conversation *summaries* — never raw transcripts — with
EMBEDDING_MODEL, AFTER PII redaction (data minimization, GDPR). This is the
privacy-sensitive step, so redaction is a precondition asserted here, not an
afterthought.
"""

from __future__ import annotations

import config


def embed_summaries(summaries: list[str], *, model: str = config.EMBEDDING_MODEL) -> list[list[float]]:
    """Return one embedding vector per (already-redacted) summary."""
    raise NotImplementedError("embed.embed_summaries — Phase 4")
