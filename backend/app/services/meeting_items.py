"""Stable document groups; task identity and eligibility are handled separately."""


def is_unfinished_extension(item, items):
    if item.get("section") != "new":
        return False
    if item.get("source", {}).get("extended_from_item_id"):
        return True
    # Older extensions kept the linked task but did not record the source row.
    return any(
        row.get("section") == "review" and row.get("status") in {"未完成", "部分完成"}
        and any(item.get(key) and item.get(key) == row.get(key) for key in ("task_id", "historical_work_id"))
        for row in items
    )


def arrangement_groups(items):
    extensions, new = [], []
    for item in items:
        if item.get("section") == "new":
            (extensions if is_unfinished_extension(item, items) else new).append(item)
    if not extensions:
        return [("", new)]
    return [(label, rows) for label, rows in (
        ("本次新建工作", new), ("上次未完成延续事项", extensions),
    ) if rows]


def ordered_arrangements(items):
    return [item for _, rows in arrangement_groups(items) for item in rows]


def continuation_order(items):
    # Display fresh work first, but keep the inherited sequence independent.
    return (
        [item for item in items if item.get("section") == "review"]
        + [item for item in items if is_unfinished_extension(item, items)]
        + [item for item in items if item.get("section") == "new" and not is_unfinished_extension(item, items)]
    )


def continuation_origin(item, items):
    if item.get("section") == "review":
        return "review"
    return "extension" if is_unfinished_extension(item, items) else "new"
