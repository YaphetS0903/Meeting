export function beijingTime(
  value: string | null | undefined,
  options: Intl.DateTimeFormatOptions = {},
) {
  if (!value) return "—";
  const timestamp = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?$/.test(
    value,
  )
    ? value.replace(" ", "T") + "Z"
    : value;
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString("zh-CN", {
    ...options,
    timeZone: "Asia/Shanghai",
    hour12: false,
  });
}
