# Copyright (c) 2026, 东篱馆主

import pytest

from gui_agent.platforms.android import UiTreeError, find_unique_text_node, parse_ui_tree


CANNED_HIERARCHY = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" resource-id="" class="android.widget.FrameLayout"
        package="com.android.settings" content-desc="" clickable="false"
        bounds="[0,0][1220,2712]">
    <node index="0" text="Settings" resource-id="com.android.settings:id/title"
          class="android.widget.TextView" package="com.android.settings"
          content-desc="" clickable="false" bounds="[42,66][246,124]" />
    <node index="1" text="Network &amp; internet" resource-id="com.android.settings:id/row_network"
          class="android.widget.LinearLayout" package="com.android.settings"
          content-desc="Network row" clickable="true" bounds="[0,200][1220,320]">
      <node index="0" text="Network &amp; internet" resource-id=""
            class="android.widget.TextView" package="com.android.settings"
            content-desc="" clickable="false" bounds="[42,220][600,300]" />
    </node>
  </node>
</hierarchy>"""


def test_parse_flattens_a_nested_hierarchy_in_document_order() -> None:
    tree = parse_ui_tree(CANNED_HIERARCHY)

    assert tree["package"] == "com.android.settings"
    assert tree["node_count"] == 4
    root, title, row, label = tree["nodes"]
    assert (root["index"], root["depth"], root["class_name"]) == (0, 0, "android.widget.FrameLayout")
    assert root["bounds"] == (0, 0, 1220, 2712)
    assert root["clickable"] is False
    assert (title["depth"], title["text"]) == (1, "Settings")
    assert title["resource_id"] == "com.android.settings:id/title"
    assert title["bounds"] == (42, 66, 246, 124)
    assert (row["index"], row["depth"], row["clickable"], row["content_desc"]) == (
        1,
        1,
        True,
        "Network row",
    )
    assert (label["depth"], label["text"]) == (2, "Network & internet")


def test_package_is_none_when_no_node_reports_one() -> None:
    tree = parse_ui_tree(
        '<hierarchy rotation="0"><node index="0" text="" resource-id="" '
        'class="android.widget.FrameLayout" package="" content-desc="" '
        'clickable="false" bounds="[0,0][10,10]" /></hierarchy>'
    )

    assert tree["package"] is None
    assert tree["node_count"] == 1


def test_malformed_xml_raises_ui_tree_error() -> None:
    with pytest.raises(UiTreeError, match="not well-formed"):
        parse_ui_tree("<hierarchy><node")


def test_non_hierarchy_root_raises_ui_tree_error() -> None:
    with pytest.raises(UiTreeError, match="hierarchy"):
        parse_ui_tree('<node bounds="[0,0][1,1]" />')


def test_empty_hierarchy_raises_ui_tree_error() -> None:
    with pytest.raises(UiTreeError, match="no nodes"):
        parse_ui_tree('<hierarchy rotation="0"></hierarchy>')


def test_malformed_bounds_raise_ui_tree_error() -> None:
    with pytest.raises(UiTreeError, match="malformed bounds"):
        parse_ui_tree(
            '<hierarchy rotation="0"><node index="0" text="" resource-id="" '
            'class="android.widget.FrameLayout" package="com.demo" content-desc="" '
            'clickable="false" bounds="0,0,10,10" /></hierarchy>'
        )


def test_find_unique_text_node_returns_the_only_exact_match() -> None:
    node = find_unique_text_node(parse_ui_tree(CANNED_HIERARCHY), "Settings")

    assert node is not None
    assert node["resource_id"] == "com.android.settings:id/title"


def test_find_unique_text_node_rejects_duplicated_text() -> None:
    assert find_unique_text_node(parse_ui_tree(CANNED_HIERARCHY), "Network & internet") is None


def test_find_unique_text_node_returns_none_for_missing_text() -> None:
    assert find_unique_text_node(parse_ui_tree(CANNED_HIERARCHY), "Absent") is None
