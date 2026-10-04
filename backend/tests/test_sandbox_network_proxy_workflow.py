from pathlib import Path

import pytest

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"


@pytest.mark.parametrize(
    "name",
    ["sandbox-network-proxy-image.yaml", "lark-cli-images.yaml", "container.yaml", "chart.yaml", "nightly.yaml"],
)
def test_independent_repository_does_not_restore_upstream_publish_workflows(name):
    """Phase one deliberately removes upstream publishing and its credentials."""
    assert not (WORKFLOWS / name).exists(), f"Review the independent release policy before restoring {name}"
