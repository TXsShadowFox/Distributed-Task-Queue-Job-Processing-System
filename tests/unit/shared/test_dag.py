import pytest

from flowforge_shared.dag import DagErrorCode, analyze_dag, descendants


def codes(nodes: list[str], edges: list[tuple[str, str]]) -> list[DagErrorCode]:
    return [e.code for e in analyze_dag(nodes, edges).errors]


def test_linear_chain_order() -> None:
    a = analyze_dag(
        ["extract", "transform", "load"], [("extract", "transform"), ("transform", "load")]
    )
    assert a.valid
    assert a.order == ("extract", "transform", "load")
    assert a.roots == ("extract",)


def test_diamond_order_is_deterministic_by_declaration() -> None:
    nodes = ["start", "b", "a", "end"]
    edges = [("start", "a"), ("start", "b"), ("a", "end"), ("b", "end")]
    a = analyze_dag(nodes, edges)
    assert a.valid
    assert a.order == ("start", "b", "a", "end")
    assert a.predecessors["end"] == ("a", "b")
    assert set(a.successors["start"]) == {"a", "b"}


def test_disconnected_components_are_allowed() -> None:
    a = analyze_dag(["x", "y", "z"], [("x", "y")])
    assert a.valid
    assert set(a.roots) == {"x", "z"}


def test_cycle_is_reported_with_path() -> None:
    result = analyze_dag(["a", "b", "c", "d"], [("a", "b"), ("b", "c"), ("c", "b"), ("c", "d")])
    assert not result.valid
    [err] = result.errors
    assert err.code is DagErrorCode.CYCLE
    assert "b -> c -> b" in err.message
    assert set(err.nodes) == {"b", "c"}
    assert result.order == ()


def test_long_cycle() -> None:
    nodes = [f"n{i}" for i in range(6)]
    edges = [(nodes[i], nodes[i + 1]) for i in range(5)] + [("n5", "n1")]
    [err] = analyze_dag(nodes, edges).errors
    assert err.code is DagErrorCode.CYCLE
    assert err.message.endswith("n1 -> n2 -> n3 -> n4 -> n5 -> n1")


def test_self_loop() -> None:
    assert codes(["a"], [("a", "a")]) == [DagErrorCode.SELF_LOOP]


def test_unknown_node_in_edge() -> None:
    assert codes(["a"], [("a", "ghost")]) == [DagErrorCode.UNKNOWN_NODE]


def test_duplicate_node_and_edge() -> None:
    assert codes(["a", "a", "b"], [("a", "b"), ("a", "b")]) == [
        DagErrorCode.DUPLICATE_NODE,
        DagErrorCode.DUPLICATE_EDGE,
    ]


@pytest.mark.parametrize("key", ["1abc", "has space", "", "a" * 65, "semi;colon"])
def test_invalid_node_keys(key: str) -> None:
    assert DagErrorCode.INVALID_NODE_KEY in codes([key], [])


def test_empty_graph() -> None:
    assert codes([], []) == [DagErrorCode.EMPTY_GRAPH]


def test_size_limits() -> None:
    nodes = [f"n{i}" for i in range(5)]
    result = analyze_dag(nodes, [("n0", "n1"), ("n1", "n2")], max_nodes=4, max_edges=1)
    assert {e.code for e in result.errors} == {
        DagErrorCode.TOO_MANY_NODES,
        DagErrorCode.TOO_MANY_EDGES,
    }


def test_reports_multiple_problems_at_once() -> None:
    result = analyze_dag(["a", "b"], [("a", "a"), ("a", "zzz"), ("a", "b"), ("b", "a")])
    assert {e.code for e in result.errors} == {
        DagErrorCode.SELF_LOOP,
        DagErrorCode.UNKNOWN_NODE,
        DagErrorCode.CYCLE,
    }


def test_large_valid_graph() -> None:
    nodes = [f"n{i}" for i in range(200)]
    edges = [(nodes[i], nodes[j]) for i in range(200) for j in (i + 1, i + 2) if j < 200]
    result = analyze_dag(nodes, edges)
    assert result.valid
    assert result.order == tuple(nodes)


def test_descendants() -> None:
    a = analyze_dag(["a", "b", "c", "d"], [("a", "b"), ("b", "c"), ("a", "d")])
    assert descendants(a.successors, "a") == {"b", "c", "d"}
    assert descendants(a.successors, "b") == {"c"}
    assert descendants(a.successors, "c") == set()
