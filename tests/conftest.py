from pathlib import Path
from unittest import mock

import pytest

from jdmtool.common import get_data_dir, get_config_dir


@pytest.fixture(autouse=True)
def mock_data_dir(tmp_path: Path):
    with mock.patch('platformdirs.user_data_dir', return_value=tmp_path / 'data'):
        get_data_dir.cache_clear()
        yield

@pytest.fixture(autouse=True)
def mock_config_dir(tmp_path: Path):
    with mock.patch('platformdirs.user_config_dir', return_value=tmp_path / 'config'):
        get_config_dir.cache_clear()
        yield
