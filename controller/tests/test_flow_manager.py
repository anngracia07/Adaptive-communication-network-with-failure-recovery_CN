import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from flow_manager import FlowManager


def test_failback_moves_only_when_strictly_better():
    assert FlowManager.should_move([1, 2, 4], [1, 3, 5, 4], 2, 3) is False
    assert FlowManager.should_move([1, 3, 5, 4], [1, 2, 4], 3, 2) is True
    assert FlowManager.should_move(None, [1, 2], float("inf"), 1) is True


def test_flows_affected_by_link():
    m = FlowManager()
    m.learn("a", "b", [1, 2, 4])
    m.learn("x", "y", [3, 5, 4])
    assert m.affected_by_link(2, 4) == ["a|b"]


def test_stale_switches():
    assert FlowManager.stale_switches([1, 2, 3, 4], [1, 5, 4]) == {2, 3}
