import { useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { parseCSV } from "../csvUtils";
import { createI18n, availableLocales, type Locale } from "../i18n";

type SortDir = "asc" | "desc";

interface DataTableProps {
  csv: string;
  suppressions?: Record<string, Record<number, string>>;
}

export function DataTable({ csv, suppressions = {} }: DataTableProps) {
  const { locale: localeParam } = useParams<{ locale: string }>();
  const locale: Locale = availableLocales.includes(localeParam as Locale)
    ? (localeParam as Locale)
    : "en";
  const i18n = useMemo(() => createI18n(locale), [locale]);
  const parsed = useMemo(() => parseCSV(csv), [csv]);

  const [sortCol, setSortCol] = useState<number | null>(null);
  const [sortDir, setSortDir] = useState<SortDir>("asc");

  const isSuppressed = (col: string, rowIdx: number): boolean =>
    suppressions[col]?.[rowIdx] !== undefined;

  const displayValue = (
    value: string | number,
    col: string,
    rowIdx: number,
  ): string => {
    if (isSuppressed(col, rowIdx)) return "—";
    if (value === "" || value === null || value === undefined) return "—";
    return String(value);
  };

  const toggleSort = (colIdx: number) => {
    if (sortCol === colIdx) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortCol(colIdx);
      setSortDir("asc");
    }
  };

  const sortedRows = useMemo(() => {
    if (sortCol === null) return parsed.rows;
    return [...parsed.rows].sort((a, b) => {
      const av = a[sortCol] ?? "";
      const bv = b[sortCol] ?? "";
      if (typeof av === "number" && typeof bv === "number") {
        return sortDir === "asc" ? av - bv : bv - av;
      }
      return sortDir === "asc"
        ? String(av).localeCompare(String(bv))
        : String(bv).localeCompare(String(av));
    });
  }, [parsed.rows, sortCol, sortDir]);

  return (
    <div className="overflow-x-auto rounded-lg border border-gray-200">
      {parsed.headers.length === 0 ? (
        <p className="text-sm text-gray-500 p-4">{i18n.t("table.noData")}</p>
      ) : (
        <table className="min-w-full text-sm divide-y divide-gray-200">
          <thead className="bg-gray-50">
            <tr>
              {parsed.headers.map((header, ci) => (
                <th
                  key={header}
                  className="px-4 py-2 text-left font-medium text-gray-600 whitespace-nowrap cursor-pointer hover:bg-gray-100 select-none"
                  onClick={() => toggleSort(ci)}
                  title={i18n.t("table.sortBy", {
                    label: i18n.columnLabel(header),
                  })}
                >
                  <span title={header}>{i18n.columnLabel(header)}</span>
                  {sortCol === ci && (
                    <span className="ml-1 text-blue-500">
                      {sortDir === "asc" ? "↑" : "↓"}
                    </span>
                  )}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="bg-white divide-y divide-gray-100">
            {sortedRows.map((row, ri) => (
              <tr key={ri} className="hover:bg-gray-50">
                {parsed.headers.map((header, ci) => {
                  const val = displayValue(row[ci], header, ri);
                  return (
                    <td
                      key={ci}
                      className={`px-4 py-2 whitespace-nowrap ${
                        val === "—" ? "text-gray-400 italic" : "text-gray-800"
                      }`}
                    >
                      {val}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
