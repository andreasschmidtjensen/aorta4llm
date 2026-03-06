"""Shared fixtures for governance tests.

Parametrizes tests across both engine backends (Python and Prolog)
to ensure behavioral equivalence.
"""

import pytest


def _prolog_available() -> bool:
    """Check if both pyswip and SWI-Prolog binary are available."""
    try:
        from pyswip import Prolog
        Prolog()
        return True
    except Exception:
        return False


_HAS_PROLOG = _prolog_available()


@pytest.fixture(params=["python", "prolog"])
def engine_backend(request):
    """Parametrize tests across both engine backends."""
    if request.param == "prolog" and not _HAS_PROLOG:
        pytest.skip("SWI-Prolog not available")
    return request.param
