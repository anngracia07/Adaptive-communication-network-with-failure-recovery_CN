import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from topology_manager import TopologyManager


def sample():
    t = TopologyManager()
    t.add_link(1, 2, 12, 21)
    t.add_link(2, 4, 24, 42)
    t.add_link(1, 3, 13, 31)
    t.add_link(3, 5, 35, 53)
    t.add_link(5, 4, 54, 45)
    return t


def test_shortest_path():
    assert sample().shortest_path(1, 4) == [1, 2, 4]


def test_path_after_link_removed():
    t = sample()
    t.remove_link(1, 2)
    assert t.shortest_path(1, 4) == [1, 3, 5, 4]


def test_no_path():
    t = sample()
    t.remove_link(1, 2)
    t.remove_link(1, 3)
    assert t.shortest_path(1, 4) is None


def test_port_removes_attached_link():
    t = sample()
    assert t.remove_port(1, 13) == [(1, 3)]
    assert t.shortest_path(1, 4) == [1, 2, 4]
