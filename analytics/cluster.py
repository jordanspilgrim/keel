"""Clustering (plan §3d, Phase 4).

sklearn KMeans over de-identified embeddings with a fixed k and random_state
(config.CLUSTER_K / CLUSTER_RANDOM_STATE) so demo runs are reproducible —
cluster instability is a named risk (plan §11). Start simple; auto-k later.
"""

from __future__ import annotations

import numpy as np
from sklearn.cluster import KMeans

import config


def cluster(vectors: list[list[float]], *, k: int = config.CLUSTER_K) -> list[int]:
    """Return a cluster label per input vector. k is clamped to the sample count."""
    if not vectors:
        return []
    x = np.array(vectors)
    k_eff = max(1, min(k, len(vectors)))
    km = KMeans(n_clusters=k_eff, random_state=config.CLUSTER_RANDOM_STATE, n_init=10)
    return km.fit_predict(x).tolist()
