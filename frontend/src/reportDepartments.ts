type Department = { id: string; name: string; parent_id?: string | null };

const isDepartmentOrFactory = (department: Department) =>
  /(?:部|工厂|厂)$/.test(department.name.trim());
const isCenter = (department: Department) =>
  /中心$/.test(department.name.trim());
const isCompany = (department: Department) =>
  /公司$/.test(department.name.trim());

export function reportDepartmentScope(departments: Department[], root: string) {
  const scope = new Set<string>();
  if (!root || !departments.some((department) => department.id === root))
    return scope;
  const children = new Map<string, string[]>();
  for (const department of departments) {
    if (department.parent_id) {
      const siblings = children.get(department.parent_id) || [];
      siblings.push(department.id);
      children.set(department.parent_id, siblings);
    }
  }
  const pending = [root];
  while (pending.length) {
    const id = pending.pop()!;
    if (scope.has(id)) continue;
    scope.add(id);
    pending.push(...(children.get(id) || []));
  }
  return scope;
}

export function reportDepartmentOptions(departments: Department[]) {
  const byId = new Map(
    departments.map((department) => [department.id, department]),
  );
  const paths = new Map<string, Department[]>();
  function path(id: string) {
    if (paths.has(id)) return paths.get(id)!;
    const result: Department[] = [];
    const visited = new Set<string>();
    let department = byId.get(id);
    while (department && !visited.has(department.id)) {
      visited.add(department.id);
      result.unshift(department);
      department = department.parent_id
        ? byId.get(department.parent_id)
        : undefined;
    }
    paths.set(id, result);
    return result;
  }
  const units = new Map<string, Department>();
  for (const department of departments) {
    const ancestors = path(department.id);
    // Stop at the first department/factory so its sections and teams stay together.
    const unit =
      ancestors.find(isDepartmentOrFactory) ||
      [...ancestors].reverse().find(isCenter) ||
      ancestors.find((item) => item.parent_id && !isCompany(item));
    if (!unit || isCompany(unit)) continue;
    units.set(unit.id, unit);
  }
  const visible = [...units.values()].filter((unit) => {
    if (!isCenter(unit)) return true;
    return ![...units.values()].some(
      (other) =>
        isDepartmentOrFactory(other) &&
        path(other.id).some((ancestor) => ancestor.id === unit.id),
    );
  });
  const options = visible.map((unit) => {
    const company = [...path(unit.id)].reverse().find(isCompany);
    return {
      id: unit.id,
      name: unit.name,
      label: company ? `${company.name} / ${unit.name}` : unit.name,
    };
  });
  const counts = new Map<string, number>();
  for (const option of options)
    counts.set(option.label, (counts.get(option.label) || 0) + 1);
  for (const option of options) {
    if (counts.get(option.label)! > 1)
      option.label = path(option.id)
        .map((department) => department.name)
        .join(" / ");
  }
  return options.sort((left, right) =>
    left.label.localeCompare(right.label, "zh-CN"),
  );
}

export function authorizedReportDepartments(
  departments: Department[],
  allowedIds: string[],
  rootIds: string[],
) {
  const allowed = new Set(allowedIds);
  const options = reportDepartmentOptions(departments).filter((d) =>
    allowed.has(d.id),
  );
  // A grant to a section never exposes its parent department. Keep that authorized
  // root selectable even when the normal organization list folds it into a parent.
  for (const id of rootIds) {
    if (
      !allowed.has(id) ||
      options.some((d) => reportDepartmentScope(departments, d.id).has(id))
    )
      continue;
    const unit = departments.find((d) => d.id === id);
    if (!unit) continue;
    const ancestors: Department[] = [];
    const visited = new Set<string>();
    let current: Department | undefined = unit;
    while (current && !visited.has(current.id)) {
      visited.add(current.id);
      ancestors.unshift(current);
      current = departments.find((d) => d.id === current?.parent_id);
    }
    // Centers/company roots with selectable descendants remain folded as before.
    if (options.some((d) => reportDepartmentScope(departments, id).has(d.id)))
      continue;
    const company = [...ancestors].reverse().find(isCompany);
    options.push({
      id,
      name: unit.name,
      label: company ? `${company.name} / ${unit.name}` : unit.name,
    });
  }
  return options.sort((a, b) => a.label.localeCompare(b.label, "zh-CN"));
}
