"""A mixed review is one document row with independently assessed people."""


def person_result(item, identifier):
    if item.get("status") == "部分完成":
        return item.get("owner_results", {}).get(identifier, "")
    return item.get("status", "")


def result_text(item):
    if item.get("status") != "部分完成":
        return item.get("status", "") or ""
    owners = item.get("owner_ids", [])
    names = dict(zip(owners, item.get("owner_names", [])))
    done = sum(person_result(item, identifier) == "已完成" for identifier in owners)
    return "\n".join([
        f"部分完成（{done}/{len(owners)}）",
        *(f"{names.get(identifier, identifier)}：{person_result(item, identifier)}" for identifier in owners),
    ])
