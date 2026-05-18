from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def tfstate_path() -> Path:
    return FIXTURES / "compute_instance.tfstate.json"


@pytest.fixture
def tfstate(tfstate_path: Path) -> dict[str, Any]:
    return json.loads(tfstate_path.read_text(encoding="utf-8"))


@pytest.fixture
def fw_subnet_tfstate() -> dict[str, Any]:
    p = FIXTURES / "firewall_subnet_drift.tfstate.json"
    return json.loads(p.read_text(encoding="utf-8"))
