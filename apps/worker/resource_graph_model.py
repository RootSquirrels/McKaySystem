"""Compatibility wrapper for resource graph builders.

This module keeps the original import path stable while the implementation now
resides in the explicit builder package under ``apps.worker.resource_graph``.
"""

from apps.worker.resource_graph import (
    GRAPH_DIRNAME,
    GRAPH_EDGES_FILENAME,
    GRAPH_NODES_FILENAME,
    ResourceGraphEdge,
    ResourceGraphNode,
    build_graph_from_findings,
    graph_dir,
    load_graph_bundle,
    write_graph_bundle,
)

__all__ = [
    "GRAPH_DIRNAME",
    "GRAPH_EDGES_FILENAME",
    "GRAPH_NODES_FILENAME",
    "ResourceGraphEdge",
    "ResourceGraphNode",
    "build_graph_from_findings",
    "graph_dir",
    "load_graph_bundle",
    "write_graph_bundle",
]
