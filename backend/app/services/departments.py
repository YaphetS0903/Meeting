from sqlalchemy import select

from app.models import Department


def department_paths(departments) -> dict[str, str]:
    values = list(departments)
    by_id = {department.id: department for department in values}
    resolved: dict[str, str] = {}

    def resolve(identifier: str, visiting: set[str] | None = None) -> str:
        if identifier in resolved:
            return resolved[identifier]
        department = by_id.get(identifier)
        if not department:
            return ""
        visiting = set(visiting or ())
        if identifier in visiting:
            return department.name
        visiting.add(identifier)
        parent = resolve(department.parent_id, visiting) if department.parent_id else ""
        resolved[identifier] = f"{parent} / {department.name}" if parent else department.name
        return resolved[identifier]

    for identifier in by_id:
        resolve(identifier)
    return resolved


def department_path(db, identifier: str | None) -> str:
    if not identifier:
        return ""
    return department_paths(db.scalars(select(Department))).get(identifier, "")
