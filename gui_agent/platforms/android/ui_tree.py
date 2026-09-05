# Copyright (c) 2026, 东篱馆主

"""Parse uiautomator hierarchy dumps into a flat, document-order node table.

Calibration and audit consumers only need to look nodes up; the nested XML
shape is therefore flattened into a list with an explicit ``depth`` field.
"""

from __future__ import annotations

import re
from typing import Any
from xml.etree import ElementTree


class UiTreeError(RuntimeError):
    """Raised when a UI hierarchy dump is empty or malformed."""


_BOUNDS_PATTERN = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")


def parse_ui_tree(xml_text: str) -> dict[str, Any]:
    """Parse a uiautomator dump into ``{"package", "node_count", "nodes"}``.

    Nodes are listed in document order; the top-level node of the hierarchy
    has depth 0.  Malformed XML, a missing ``<hierarchy>`` root, a dump with
    zero nodes, or malformed bounds raise :class:`UiTreeError`.
    """

    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError as error:
        raise UiTreeError("UI hierarchy payload is not well-formed XML") from error
    if root.tag != "hierarchy":
        raise UiTreeError("UI hierarchy payload has no <hierarchy> root")

    nodes: list[dict[str, Any]] = []
    package: str | None = None

    def visit(element: ElementTree.Element, depth: int) -> None:
        nonlocal package
        nodes.append(_parse_node(element, depth, len(nodes)))
        if package is None:
            package = element.get("package") or None
        for child in element:
            if child.tag == "node":
                visit(child, depth + 1)

    for child in root:
        if child.tag == "node":
            visit(child, 0)

    if not nodes:
        raise UiTreeError("UI hierarchy contains no nodes")
    return {"package": package, "node_count": len(nodes), "nodes": nodes}


def find_unique_text_node(tree: dict[str, Any], text: str) -> dict[str, Any] | None:
    """Return the only node whose text matches exactly, else None.

    A text that is absent or carried by more than one node is not a usable
    calibration target, so both cases return None.
    """

    matches = [node for node in tree["nodes"] if node["text"] == text]
    if len(matches) != 1:
        return None
    return matches[0]


def _parse_node(element: ElementTree.Element, depth: int, position: int) -> dict[str, Any]:
    bounds_match = _BOUNDS_PATTERN.fullmatch(element.get("bounds", ""))
    if bounds_match is None:
        raise UiTreeError(f"UI hierarchy node {position} has malformed bounds")
    left, top, right, bottom = (int(group) for group in bounds_match.groups())
    try:
        index = int(element.get("index", "0"))
    except ValueError as error:
        raise UiTreeError(f"UI hierarchy node {position} has a malformed index") from error
    return {
        "index": index,
        "text": element.get("text", ""),
        "resource_id": element.get("resource-id", ""),
        "class_name": element.get("class", ""),
        "content_desc": element.get("content-desc", ""),
        "clickable": element.get("clickable", "false") == "true",
        "bounds": (left, top, right, bottom),
        "depth": depth,
    }
