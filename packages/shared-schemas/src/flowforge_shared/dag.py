"""Directed-acyclic-graph validation for workflow definitions.

``analyze_dag`` reports *all* structural problems at once (so the workflow builder can highlight them together)
and, when the graph is valid, a deterministic topological order (Kahn's algorithm, ties broken by declaration
order). Cycles are reported with a concrete path, e.g. ``extract -> transform -> extract``.
"""

from __future__ import annotations

import heapq
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

NODE_KEY_PATTERN = r"^[A-Za-z][A-Za-z0-9_-]{0,63}$"
_NODE_KEY_RE = re.compile(NODE_KEY_PATTERN)
DEFAULT_MAX_NODES = 200
DEFAULT_MAX_EDGES = 1000


class DagErrorCode(StrEnum):
    EMPTY_GRAPH = "EMPTY_GRAPH"
    TOO_MANY_NODES = "TOO_MANY_NODES"
    TOO_MANY_EDGES = "TOO_MANY_EDGES"
    INVALID_NODE_KEY = "INVALID_NODE_KEY"
    DUPLICATE_NODE = "DUPLICATE_NODE"
    UNKNOWN_NODE = "UNKNOWN_NODE"
    SELF_LOOP = "SELF_LOOP"
    DUPLICATE_EDGE = "DUPLICATE_EDGE"
    CYCLE = "CYCLE"


@dataclass(frozen=True, slots=True)
class DagError:
    code: DagErrorCode
    message: str
    nodes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DagAnalysis:
    errors: tuple[DagError, ...]
    order: tuple[str, ...] = ()
    predecessors: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    successors: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    @property
    def valid(self) -> bool:
        return not self.errors

    @property
    def roots(self) -> tuple[str, ...]:
        return tuple(n for n in self.order if not self.predecessors.get(n))


def analyze_dag(
    nodes: Sequence[str],
    edges: Sequence[tuple[str, str]],
    *,
    max_nodes: int = DEFAULT_MAX_NODES,
    max_edges: int = DEFAULT_MAX_EDGES,
) -> DagAnalysis:
    errors: list[DagError] = []
    if not nodes:
        return DagAnalysis(errors=(DagError(DagErrorCode.EMPTY_GRAPH, "workflow has no nodes"),))
    if len(nodes) > max_nodes:
        errors.append(
            DagError(
                DagErrorCode.TOO_MANY_NODES, f"workflow has {len(nodes)} nodes (max {max_nodes})"
            )
        )
    if len(edges) > max_edges:
        errors.append(
            DagError(
                DagErrorCode.TOO_MANY_EDGES, f"workflow has {len(edges)} edges (max {max_edges})"
            )
        )

    index: dict[str, int] = {}
    for i, key in enumerate(nodes):
        if not _NODE_KEY_RE.fullmatch(key):
            errors.append(
                DagError(
                    DagErrorCode.INVALID_NODE_KEY,
                    f"invalid node key {key!r}: must start with a letter and contain only "
                    "letters, digits, '-' or '_' (max 64 chars)",
                    (key,),
                )
            )
        if key in index:
            errors.append(
                DagError(DagErrorCode.DUPLICATE_NODE, f"duplicate node key {key!r}", (key,))
            )
        else:
            index[key] = i

    preds: dict[str, list[str]] = {k: [] for k in index}
    succs: dict[str, list[str]] = {k: [] for k in index}
    seen_edges: set[tuple[str, str]] = set()
    for src, dst in edges:
        unknown = [n for n in (src, dst) if n not in index]
        if unknown:
            errors.append(
                DagError(
                    DagErrorCode.UNKNOWN_NODE,
                    f"edge {src!r} -> {dst!r} references unknown node(s) {', '.join(map(repr, unknown))}",
                    (src, dst),
                )
            )
            continue
        if src == dst:
            errors.append(
                DagError(DagErrorCode.SELF_LOOP, f"node {src!r} depends on itself", (src,))
            )
            continue
        if (src, dst) in seen_edges:
            errors.append(
                DagError(
                    DagErrorCode.DUPLICATE_EDGE, f"duplicate edge {src!r} -> {dst!r}", (src, dst)
                )
            )
            continue
        seen_edges.add((src, dst))
        succs[src].append(dst)
        preds[dst].append(src)

    order = _topological_order(index, preds, succs)
    if len(order) < len(index):
        remaining = {k for k in index if k not in set(order)}
        cycle = _find_cycle(remaining, succs, index)
        errors.append(
            DagError(
                DagErrorCode.CYCLE,
                "workflow contains a cycle: " + " -> ".join(cycle),
                tuple(cycle[:-1]),
            )
        )

    frozen_preds = {k: tuple(v) for k, v in preds.items()}
    frozen_succs = {k: tuple(v) for k, v in succs.items()}
    if errors:
        return DagAnalysis(errors=tuple(errors), predecessors=frozen_preds, successors=frozen_succs)
    return DagAnalysis(
        errors=(), order=tuple(order), predecessors=frozen_preds, successors=frozen_succs
    )


def _topological_order(
    index: Mapping[str, int],
    preds: Mapping[str, list[str]],
    succs: Mapping[str, list[str]],
) -> list[str]:
    in_degree = {k: len(v) for k, v in preds.items()}
    heap = [(index[k], k) for k, d in in_degree.items() if d == 0]
    heapq.heapify(heap)
    order: list[str] = []
    while heap:
        _, node = heapq.heappop(heap)
        order.append(node)
        for nxt in succs[node]:
            in_degree[nxt] -= 1
            if in_degree[nxt] == 0:
                heapq.heappush(heap, (index[nxt], nxt))
    return order


def _find_cycle(
    candidates: set[str], succs: Mapping[str, list[str]], index: Mapping[str, int]
) -> list[str]:
    """Return one concrete cycle (first node repeated at the end) among ``candidates``."""
    white, grey, black = 0, 1, 2
    color = dict.fromkeys(candidates, white)
    for start in sorted(candidates, key=lambda k: index[k]):
        if color[start] != white:
            continue
        stack: list[tuple[str, Iterable[str]]] = [(start, iter(succs[start]))]
        path = [start]
        color[start] = grey
        while stack:
            node, it = stack[-1]
            advanced = False
            for nxt in it:
                if nxt not in color:
                    continue
                if color[nxt] == grey:
                    return [*path[path.index(nxt) :], nxt]
                if color[nxt] == white:
                    color[nxt] = grey
                    path.append(nxt)
                    stack.append((nxt, iter(succs[nxt])))
                    advanced = True
                    break
            if not advanced:
                color[node] = black
                path.pop()
                stack.pop()
    # Kahn's algorithm guarantees a cycle exists among the candidates; this is unreachable in practice.
    first = min(candidates, key=lambda k: index[k])
    return [first, first]


def descendants(successors: Mapping[str, Sequence[str]], start: str) -> set[str]:
    """All nodes reachable from ``start`` (excluding ``start``)."""
    seen: set[str] = set()
    stack = list(successors.get(start, ()))
    while stack:
        node = stack.pop()
        if node in seen:
            continue
        seen.add(node)
        stack.extend(successors.get(node, ()))
    return seen
