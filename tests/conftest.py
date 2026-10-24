import pytest
from hypothesis import settings

# The tools are fast; timing deadlines only make property tests flaky on slow machines.
settings.register_profile("silverfile", deadline=None, max_examples=200)
settings.load_profile("silverfile")


@pytest.fixture
def anyio_backend():
    return "asyncio"
