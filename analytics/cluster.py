"""Clustering (plan §3d, Phase 4).

sklearn KMeans over the de-identified embeddings, fixed k and random_state
(config.CLUSTER_K / CLUSTER_RANDOM_STATE) so demo runs are reproducible —
cluster instability is a named risk (plan §11). Start simple; auto-k is a
later refinement (handoff §8 open decision).
"""

from __future__ import annotations

import config


def cluster(vectors: list[list[float]], *, k: int = config.CLUSTER_K) -> list[int]:
    """Return a cluster label per input vector."""
    raise NotImplementedError("cluster.cluster — Phase 4")
