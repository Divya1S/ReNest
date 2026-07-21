export function formatDateTime(value: string | null | undefined): string {
  const date = new Date(value ?? "");
  if (!value || isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(date);
}

export function formatCurrencyValue(amount: number | string | null | undefined): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  }).format(Number(amount || 0));
}

export function formatMoney(type: string, amount: number | string | null | undefined): string {
  if (type === "free") return "Free";
  return formatCurrencyValue(amount);
}

export function formatLabel(value: string | null | undefined): string {
  if (!value) return "";
  return value.replaceAll("_", " ").replace(/\b\w/g, (c: string) => c.toUpperCase());
}
