import pytest
from pathlib import Path


@pytest.fixture
def project_root() -> Path:
    return Path(__file__).parent.parent


@pytest.fixture
def raw_data_path(project_root) -> Path:
    return project_root / "data" / "raw"


@pytest.fixture
def out_data_path(project_root) -> Path:
    return project_root / "data" / "out"
