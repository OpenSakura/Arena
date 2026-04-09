from __future__ import annotations

from pathlib import Path

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-e2e",
        action="store_true",
        default=False,
        help="Run docker-backed end-to-end tests.",
    )


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    run_e2e = bool(config.getoption("--run-e2e"))
    skip_e2e = pytest.mark.skip(reason="Pass --run-e2e to run e2e tests")

    for item in items:
        parts = Path(str(item.path)).parts
        if "e2e" in parts:
            item.add_marker(pytest.mark.e2e)
            if not run_e2e:
                item.add_marker(skip_e2e)
        else:
            item.add_marker(pytest.mark.unit)
