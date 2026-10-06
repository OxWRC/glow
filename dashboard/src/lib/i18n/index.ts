import { en, type Messages } from "./en";

const messages = { en };
const PHASE_PREFIX_RE = /^(baseline|comparison|change)_(.+)$/;

export type Locale = keyof typeof messages;

export const availableLocales = Object.keys(messages) as Locale[];

function lookupText(dictionary: Messages, key: string): string | undefined {
  let current: unknown = dictionary;

  for (const segment of key.split(".")) {
    if (
      typeof current !== "object" ||
      current === null ||
      !(segment in current)
    ) {
      return undefined;
    }
    current = (current as Record<string, unknown>)[segment];
  }

  return typeof current === "string" ? current : undefined;
}

function interpolate(
  template: string,
  params: Record<string, string | number> = {},
): string {
  return template.replace(/\{(\w+)\}/g, (_, key: string) =>
    String(params[key] ?? `{${key}}`),
  );
}

function humanizeIdentifier(value: string): string {
  if (!value.trim()) {
    return "";
  }

  return value
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/_/g, " ")
    .trim()
    .replace(/\s+/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function formatColumnLabel(column: string, dictionary: Messages): string {
  const trimmed = column.trim();
  if (!trimmed) {
    return "";
  }

  if (trimmed.includes("__")) {
    const [formId, rawColumn] = trimmed.split("__", 2);
    const formLabels = (dictionary as unknown as Record<string, unknown>)
      .forms as Record<string, string> | undefined;
    const formLabel = formLabels?.[formId] ?? humanizeIdentifier(formId);
    return `${formLabel}: ${formatColumnLabel(rawColumn, dictionary)}`;
  }

  const directLabels = dictionary.columns as Record<string, string>;
  if (trimmed in directLabels) {
    return directLabels[trimmed];
  }

  const prefixed = PHASE_PREFIX_RE.exec(trimmed);
  if (prefixed) {
    const [, phase, baseColumn] = prefixed;
    const phaseLabels = (dictionary as unknown as Record<string, unknown>)
      .phases as Record<string, string> | undefined;
    return `${phaseLabels?.[phase] ?? humanizeIdentifier(phase)}: ${formatColumnLabel(baseColumn, dictionary)}`;
  }

  return humanizeIdentifier(trimmed);
}

function csvToColumns(value: string): string[] {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

export function createI18n(activeLocale: Locale) {
  const dictionary = messages[activeLocale];

  return {
    locale: activeLocale,
    t(key: string, params?: Record<string, string | number>) {
      const template = lookupText(dictionary, key);
      return template ? interpolate(template, params) : key;
    },
    columnLabel(column: string) {
      return formatColumnLabel(column, dictionary);
    },
    columnText(value: string) {
      return csvToColumns(value)
        .map((column) => formatColumnLabel(column, dictionary))
        .join(" | ");
    },
    chartFormatters: {
      columnLabel: (column: string) => formatColumnLabel(column, dictionary),
      countLabel: lookupText(dictionary, "chart.count") ?? "Count",
      meanLabel: lookupText(dictionary, "chart.mean") ?? "Mean",
      periodLabel: lookupText(dictionary, "chart.period") ?? "Period",
    },
  };
}
