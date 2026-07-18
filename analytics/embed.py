"""De-identified embedding (plan §3d, §5, Phase 4).

Embeds conversation *summaries* — never raw transcripts — with EMBEDDING_MODEL.
The summaries are already PII-redacted upstream (data minimization, GDPR). One
batched embeddings call covers the whole set.
"""

from __future__ import annotations

import config
import llm


def embed_summaries(summaries: list[str], *, model: str = config.EMBEDDING_MODEL) -> list[list[float]]:
    """Return one embedding vector per (already de-identified) summary."""
    if not summaries:
        return []
    resp = llm.client().embeddings.create(model=model, input=summaries)
    return [d.embedding for d in resp.data]
