"""Shared fixtures for governance tests.

Parametrizes tests across both engine backends (Python and Prolog)
to ensure behavioral equivalence.
"""

import pytest


@pytest.fixture(params=["python", "prolog"])
def engine_backend(request):
    """Parametrize tests across both engine backends."""
    if request.param == "prolog":
        pytest.importorskip("pyswip")
    return request.param
