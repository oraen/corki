// Form values are presentation data; the Python host validates against the
// original core-owned schema before routing any response to the runtime.
export function defaults(fields: Record<string, any>): Record<string, any> {
  return Object.fromEntries(
    Object.entries(fields)
      .filter(([, field]) => field.default !== undefined)
      .map(([key, field]) => [key, field.default]),
  );
}
export function choices(field: any): { id: string; label: string }[] {
  if (field.enum)
    return field.enum.map((v: any, i: number) => ({
      id: String(v),
      label: field.enumNames?.[i] || String(v),
    }));
  return (field.oneOf || field.anyOf || []).map((v: any) => ({
    id: String(v.const),
    label: v.title,
  }));
}
export function formContent(
  fields: Record<string, any>,
  answers: Record<string, any>,
) {
  const result: Record<string, any> = Object.create(null);
  for (const [key, field] of Object.entries(fields)) {
    const value = answers[key];
    if (value === undefined || value === "") continue;
    if (field.type === "number" || field.type === "integer") {
      const number = Number(value);
      if (
        !Number.isFinite(number) ||
        (field.type === "integer" && !Number.isSafeInteger(number))
      )
        throw new Error(
          `${field.title || key} 需要有效的${field.type === "integer" ? "整数" : "数字"}`,
        );
      result[key] = number;
    } else if (field.type === "boolean")
      result[key] = value === true || value === "true";
    else result[key] = value;
  }
  return result;
}
