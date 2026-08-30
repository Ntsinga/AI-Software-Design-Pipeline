from pathlib import Path

import pytest

from design_pipeline.runtime import DesignRuntime


@pytest.fixture
def runtime(tmp_path: Path) -> DesignRuntime:
    instance = DesignRuntime(tmp_path)
    instance.initialize("test-project")
    return instance

