export type DirectoryPerson = {
  id: string;
  name: string;
  active: boolean;
  department_id?: string | null;
  department_ids?: string[];
};

const nonEmployeeNames = new Set([
  "oa",
  "ceshi",
  "cs",
  "测试",
  "管理员",
  "董事长信箱",
]);

// Some option APIs omit position and active. Enrich only the supplied IDs,
// preserving the endpoint's scope and its current values.
export function withDirectoryDetails(
  people: DirectoryPerson[],
  directory: (DirectoryPerson & { position?: string })[],
) {
  const known = new Map(directory.map((person) => [person.id, person]));
  return people.map((person) => ({ ...known.get(person.id), ...person }));
}

export function isEmployeeAccount(person: {
  name?: string;
  position?: string;
}) {
  const normalize = (value?: string) =>
    String(value || "")
      .normalize("NFKC")
      .trim()
      .toLowerCase();
  const name = normalize(person.name);
  // Exclude known service identities, without guessing from name length or script.
  // The identities themselves remain available to existing historical records.
  return (
    Boolean(name) &&
    !nonEmployeeNames.has(name) &&
    !name.includes("机器人") &&
    !/^seeyon\d*$/.test(name) &&
    !/^(?:rpa|robot|bot)(?:[\s_-]*\d+)?$/.test(name) &&
    !/^(?:测试|ceshi|test|cs)(?:账号|账户)?[\s_-]*\d*$/.test(name) &&
    !["虚拟岗位", "rpa机器人"].includes(normalize(person.position))
  );
}

export function pastedPeople(
  text: string,
  users: (DirectoryPerson & { label?: string })[],
) {
  const names = text
    .trim()
    .split(/[、,，;；/／|｜\r\n\t]+/u)
    .flatMap((part) => {
      const name = part.trim();
      // Keep a copied, disambiguated label such as “张三 · 信息部” intact.
      return users.some((user) => user.name === name || user.label === name)
        ? [name]
        : name.split(/\s+/u);
    })
    .filter(Boolean);
  const ids: string[] = [],
    unresolved: string[] = [],
    ambiguous: string[] = [];
  for (const name of names) {
    const matches = users.filter(
      (user) => user.name === name || user.label === name,
    );
    if (matches.length === 1) ids.push(matches[0].id);
    else if (matches.length > 1) ambiguous.push(name);
    else unresolved.push(name);
  }
  return { names, ids: [...new Set(ids)], unresolved, ambiguous };
}
