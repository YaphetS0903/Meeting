from app.models import Department
from app.services.departments import department_paths


def test_department_paths_include_every_parent(db):
    root = Department(id="department-root", name="集团")
    factory = Department(
        id="department-factory",
        name="外饰工厂",
        parent_id=root.id,
    )
    team = Department(
        id="department-team",
        name="人力资源部",
        parent_id=factory.id,
    )
    db.add_all([root, factory, team])
    db.flush()

    paths = department_paths([root, factory, team])

    assert paths[root.id] == "集团"
    assert paths[factory.id] == "集团 / 外饰工厂"
    assert paths[team.id] == "集团 / 外饰工厂 / 人力资源部"
