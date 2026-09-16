import pytest

from egr.core.config import EGRConfig, Settings
from egr.runtime.runtime import Runtime
from egr.templates import render_workspace


@pytest.fixture()
def workspace(tmp_path):
    render_workspace(tmp_path, sample=True)
    return tmp_path


@pytest.fixture()
def runtime(workspace):
    settings = Settings(workspace=workspace, config=EGRConfig())
    instance = Runtime(settings, enable_logging=False)
    yield instance
    instance.close()
