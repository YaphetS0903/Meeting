/** Accept common copied mailbox formats without accepting header control characters. */
export function normalizeMailbox(raw: string): string | null {
  if (/[\u0000-\u001f\u007f]/.test(raw)) return null;
  let value = raw.normalize("NFKC").trim();
  const named = value.match(/^[^<>]*<([^<>]+)>$/u);
  if (named) value = named[1].trim();
  value = value.replace(/^mailto:/i, "");
  if (
    value.length > 254 ||
    !/^[^\s@<>,;:"\\]+@[^\s@<>,;:"\\]+\.[^\s@<>,;:"\\]+$/u.test(value)
  )
    return null;
  const [local, domain] = value.split("@");
  if (
    domain
      .split(".")
      .some((part) => !part || part.startsWith("-") || part.endsWith("-"))
  )
    return null;
  return `${local}@${domain.toLowerCase()}`;
}
