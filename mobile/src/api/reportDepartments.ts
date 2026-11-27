type Department = { id: string; name: string; parent_id?: string | null };

const isDepartmentOrFactory = (d: Department) => /(?:部|工厂|厂)$/.test(d.name.trim());
const isCenter = (d: Department) => /中心$/.test(d.name.trim());
const isCompany = (d: Department) => /公司$/.test(d.name.trim());

export function reportDepartmentScope(departments: Department[], root: string) {
  const scope = new Set<string>();
  if (!root || !departments.some((d) => d.id === root)) return scope;
  const children = new Map<string, string[]>();
  for (const d of departments) {
    if (d.parent_id) {
      const list = children.get(d.parent_id) || [];
      list.push(d.id);
      children.set(d.parent_id, list);
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
  const byId = new Map(departments.map((d) => [d.id, d]));
  const paths = new Map<string, Department[]>();
  function path(id: string) {
    if (paths.has(id)) return paths.get(id)!;
    const result: Department[] = [];
    const visited = new Set<string>();
    let cur = byId.get(id);
    while (cur && !visited.has(cur.id)) {
      visited.add(cur.id);
      result.unshift(cur);
      cur = cur.parent_id ? byId.get(cur.parent_id) : undefined;
    }
    paths.set(id, result);
    return result;
  }
  const units = new Map<string, Department>();
  for (const d of departments) {
    const ancestors = path(d.id);
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
  for (const opt of options) counts.set(opt.label, (counts.get(opt.label) || 0) + 1);
  for (const opt of options) {
    if (counts.get(opt.label)! > 1)
      opt.label = path(opt.id)
        .map((d) => d.name)
        .join(" / ");
  }
  return options.sort((a, b) => a.label.localeCompare(b.label, "zh-CN"));
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
    let cur: Department | undefined = unit;
    while (cur && !visited.has(cur.id)) {
      visited.add(cur.id);
      ancestors.unshift(cur);
      cur = departments.find((d) => d.id === cur?.parent_id);
    }
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

export function sortReviewsByResult<T extends { result?: string }>(rows: T[]): T[] {
  const order: Record<string, number> = {
    已完成: 0,
    未完成: 1,
    未到期: 2,
    不统计: 3,
    关闭: 4,
    已关闭: 4,
  };
  return rows
    .map((row, idx) => ({ row, idx }))
    .sort((a, b) => {
      const ra = a.row.result && a.row.result in order ? order[a.row.result] : 99;
      const rb = b.row.result && b.row.result in order ? order[b.row.result] : 99;
      return ra !== rb ? ra - rb : a.idx - b.idx;
    })
    .map((x) => x.row);
}
