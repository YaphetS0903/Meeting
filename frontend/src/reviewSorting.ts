type ReviewRow = {
  id: string;
  owner_ids?: string[];
  source?: { continuation_origin?: string };
};
type OwnerGroup<T> = { key: string; anchor?: string; rows: T[] };

const owners = (row: ReviewRow) => [...new Set(row.owner_ids || [])].sort();
const ownerKey = (row: ReviewRow) => {
  const ids = owners(row);
  return ids.length ? JSON.stringify(ids) : `unassigned:${row.id}`;
};

/** A shared row can stay in the neighbouring person's historical block. */
function historicalGroups<T extends ReviewRow>(
  rows: T[],
): OwnerGroup<T>[] | null {
  const right: (string | undefined)[] = [];
  let next: string | undefined;
  for (let i = rows.length - 1; i >= 0; i--) {
    right[i] = next;
    const ids = owners(rows[i]);
    if (ids.length === 1) next = ids[0];
  }
  const groups: OwnerGroup<T>[] = [];
  const seen = new Set<string>();
  let left: string | undefined;
  for (let i = 0; i < rows.length; i++) {
    const row = rows[i],
      ids = owners(row);
    const current = groups.at(-1);
    const candidates = ids.filter((id) => id === left || id === right[i]);
    const anchor =
      ids.length === 1
        ? ids[0]
        : current?.anchor && candidates.includes(current.anchor)
          ? current.anchor
          : candidates.find((id) => id === left) || candidates[0];
    const key = anchor ? `person:${anchor}` : ownerKey(row);
    if (key !== current?.key) {
      // A person (or a shared-only combination) reappearing means it is scattered.
      if (seen.has(key)) return null;
      seen.add(key);
      groups.push({ key, anchor, rows: [] });
    }
    groups.at(-1)!.rows.push(row);
    if (ids.length === 1) left = ids[0];
  }
  return groups;
}

export function orderReviewByOwners<T extends ReviewRow>(
  rows: T[],
  origins: Record<string, string> = {},
): T[] {
  const originRank = (row: T) =>
    ({ review: 0, extension: 1, new: 2 })[
      (origins[row.id] || row.source?.continuation_origin) as
        "review" | "extension" | "new"
    ] ?? 0;
  const historyRows = (source: T[]) => {
    const inherited = source.filter((row) => originRank(row) === 0);
    // Imported history may have no provenance; its existing order is the evidence.
    return inherited.length ? inherited : source;
  };
  const preserve = (groups: OwnerGroup<T>[], history: T[], source: T[]) => {
    const historicalRows = new Set(history);
    const byCombination = new Map<string, OwnerGroup<T>>();
    for (const group of groups)
      for (const row of group.rows)
        if (!byCombination.has(ownerKey(row)))
          byCombination.set(ownerKey(row), group);
    const additions = source
      .filter((row) => !historicalRows.has(row))
      .sort((a, b) => originRank(a) - originRank(b));
    for (const row of additions) {
      const ids = owners(row),
        key = ownerKey(row);
      let group =
        byCombination.get(key) ||
        groups.find((g) => g.anchor && ids.includes(g.anchor));
      if (!group) {
        group = { key, rows: [] };
        groups.push(group);
      }
      group.rows.push(row);
      byCombination.set(key, group);
    }
    return groups.flatMap((group) => group.rows);
  };
  const history = historyRows(rows),
    groups = historicalGroups(history);
  if (groups) return preserve(groups, history, rows);
  // Scattered history uses the established grouping and continuation priority.
  const normal = new Map<string, T[]>();
  for (const row of rows) {
    const key = ownerKey(row);
    if (!normal.has(key)) normal.set(key, []);
    normal.get(key)!.push(row);
  }
  const ordered = [...normal.values()].flatMap((group) =>
    group.sort((a, b) => originRank(a) - originRank(b)),
  );
  // Integrate additions in the same pass so a second click cannot move them again.
  const normalHistory = historyRows(ordered),
    normalGroups = historicalGroups(normalHistory);
  return normalGroups
    ? preserve(normalGroups, normalHistory, ordered)
    : ordered;
}
