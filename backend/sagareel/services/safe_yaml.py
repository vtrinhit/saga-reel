"""Đọc YAML do người dùng đưa vào (file khai báo project, file nhập clip) có giới hạn kích thước **sau khi nở alias**
(chống "billion laughs": vài KB anchor/alias nở thành hàng tỷ phần tử khi pydantic/json duyệt). Anchor/alias nhỏ hợp
lệ vẫn đọc được (vd. `yaml.safe_dump` tự sinh khi hai chỗ dùng chung một object); alias đệ quy bị từ chối. File YAML
đóng gói của app vẫn dùng `yaml.safe_load`."""

from __future__ import annotations

from typing import Any

import yaml

MAX_NODES = 500_000
_Loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)  # libyaml nhanh hơn nhiều khi có


def _expanded_size(root: yaml.Node, limit: int) -> int:
    """Số nút nếu nở hết alias (nút dùng chung đếm mỗi lần dùng). Vượt `limit` / alias đệ quy → `ValueError`."""
    memo: dict[int, int] = {}
    visiting: set[int] = set()

    def size(node: yaml.Node) -> int:
        key = id(node)
        if key in memo:
            return memo[key]
        if key in visiting:
            raise ValueError("YAML có alias đệ quy (tự tham chiếu).")
        visiting.add(key)
        total = 1
        if isinstance(node, yaml.SequenceNode):
            for child in node.value:
                total += size(child)
                if total > limit:
                    break
        elif isinstance(node, yaml.MappingNode):
            for k, v in node.value:
                total += size(k) + size(v)
                if total > limit:
                    break
        visiting.discard(key)
        if total > limit:
            raise ValueError(f"YAML quá lớn sau khi nở alias (hơn {limit} nút).")
        memo[key] = total
        return total

    return size(root)


def load(text: str, *, max_nodes: int = MAX_NODES) -> Any:
    """Như `yaml.safe_load` (một tài liệu) nhưng tài liệu nở alias quá `max_nodes` nút hoặc alias đệ quy →
    `ValueError` (tiếng Việt). Lỗi cú pháp vẫn là `yaml.YAMLError`."""
    loader = _Loader(text)
    try:
        node = loader.get_single_node()
        if node is None:
            return None
        _expanded_size(node, max_nodes)
        return loader.construct_document(node)
    finally:
        loader.dispose()
