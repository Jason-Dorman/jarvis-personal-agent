import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def load_json(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def stablecoins() -> list[dict]:
    return load_json("defillama_stablecoins.json")["peggedAssets"]
