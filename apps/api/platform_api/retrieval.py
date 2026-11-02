"""Offline lexical hashing, deliberately not a semantic embedding model."""

import hashlib
import math
import re

DIMENSIONS = 128


def embed(text: str) -> list[float]:
    vector = [0.0] * DIMENSIONS
    for token in re.findall(r"\w+", text.casefold()):
        digest = hashlib.sha256(token.encode()).digest()
        vector[int.from_bytes(digest[:4], "big") % DIMENSIONS] += 1
    norm = math.sqrt(sum(value * value for value in vector))
    if not norm:
        raise ValueError("Text must contain at least one word")
    return [value / norm for value in vector]


def vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(str(value) for value in vector) + "]"
