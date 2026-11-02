import math

import pytest
from platform_api.retrieval import DIMENSIONS, embed


def test_embedding_stable_normalized_and_case_insensitive():
    vector = embed("PostgreSQL Backup recovery")
    assert len(vector) == DIMENSIONS
    assert vector == embed("postgresql backup RECOVERY")
    assert math.isclose(sum(x * x for x in vector), 1)
    assert vector != embed("metrics dashboard latency")


@pytest.mark.parametrize("text", ["", "   ", "?!"])
def test_zero_vectors_rejected(text):
    with pytest.raises(ValueError):
        embed(text)
