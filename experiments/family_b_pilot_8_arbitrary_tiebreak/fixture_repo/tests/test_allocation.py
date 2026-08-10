import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from allocation.pick_winner import pick_winner


def test_highest_score_wins():
    candidates = [
        {"id": 1, "score": 10},
        {"id": 2, "score": 50},
    ]
    assert pick_winner(candidates)["id"] == 2
