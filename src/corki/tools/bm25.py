"""Scoring semantics of bm25 2.3.2 (MIT; see BM25_LICENSE.txt).

Tokenization is deliberately separate: the same token identity must be used for
document and query input. Scores retain the upstream f32 arithmetic boundaries.
"""

import math
import struct
from collections import Counter
from collections.abc import Hashable, Iterable


def _f32(value: float) -> float:
    return struct.unpack("f", struct.pack("f", value))[0]


class BM25Scorer:
    def __init__(self, documents: Iterable[Iterable[Hashable]]) -> None:
        counts = tuple(Counter(document) for document in documents)
        self.average_length = (
            _f32(sum(doc.total() for doc in counts) / len(counts)) if counts else 256.0
        )
        average = self.average_length if self.average_length > 0 else 256.0
        k1, b = _f32(1.2), _f32(0.75)
        self._weights = []
        self._postings: dict[Hashable, set[int]] = {}
        for index, document in enumerate(counts):
            length_ratio = _f32(_f32(document.total()) / average)
            norm = _f32(k1 * _f32(_f32(1.0 - b) + _f32(b * length_ratio)))
            weights = {}
            for term, count in document.items():
                frequency = _f32(count)
                numerator = _f32(frequency * _f32(k1 + 1.0))
                weights[term] = _f32(numerator / _f32(frequency + norm))
                self._postings.setdefault(term, set()).add(index)
            self._weights.append(weights)
        self._idf = {}
        for term, postings in self._postings.items():
            frequency = _f32(len(postings))
            numerator = _f32(_f32(_f32(len(counts)) - frequency) + 0.5)
            denominator = _f32(frequency + 0.5)
            self._idf[term] = _f32(math.log(_f32(1.0 + _f32(numerator / denominator))))

    def rank(self, query: Iterable[Hashable], limit: int) -> tuple[tuple[int, float], ...]:
        if limit <= 0:
            return ()
        # Upstream loops over query embedding.indices(), including repeats.
        # Query TF-normalized embedding values are not used in its score.
        terms = tuple(query)
        candidates: set[int] = set()
        for term in terms:
            candidates.update(self._postings.get(term, ()))
        ranked = []
        for index in candidates:
            score = 0.0
            weights = self._weights[index]
            for term in terms:
                score = _f32(score + _f32(self._idf.get(term, 0.0) * weights.get(term, 0.0)))
            ranked.append((index, score))
        # Rust's HashSet-backed ties are unspecified. Stable registration order
        # provides deterministic Corki ties without claiming upstream tie parity.
        ranked.sort(key=lambda pair: (-pair[1], pair[0]))
        return tuple(ranked[:limit])
