"""The state reducer must merge concurrent writes from the parallel extraction branch
correctly, whatever order they arrive in (specification section 4.1).

TODO (owner of src/state.py).
"""

import pytest


@pytest.mark.skip(reason="placeholder: not written yet")
def test_parallel_extraction_results_merge_in_any_order():
    ...
