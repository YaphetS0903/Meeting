type WorkContent = {
  category?: string | null;
  content?: string | null;
};

export function mergedWorkContent(item: WorkContent): string {
  const category = (item.category || "").trim();
  const content = item.content || "";
  if (!category || category === "/" || content.trimStart().startsWith(category))
    return content;
  return `${category}\n${content}`.trim();
}

export function updateWorkContent(item: WorkContent, value: string): void {
  item.category = "";
  item.content = value;
}
