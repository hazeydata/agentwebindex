import pytest

@pytest.fixture(autouse=True)
def _unittest_tmp_path(request, tmp_path):
    if request.instance is not None: request.instance.tmp_path = tmp_path
