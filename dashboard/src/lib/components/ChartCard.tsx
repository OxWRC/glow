import { useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { Bar, Line } from "react-chartjs-2";
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  BarElement,
  LineElement,
  PointElement,
  Title,
  Tooltip,
  Legend,
} from "chart.js";
import type { ChartJsData } from "../chartUtils";
import { downloadCSV } from "../csvUtils";
import { createI18n, availableLocales, type Locale } from "../i18n";
import { DataTable } from "./DataTable";

// Registered here (module scope), not in main.tsx: Storybook's preview never
// loads main.tsx, so registering there would leave every story's chart
// unrendered once Storybook is wired up (Task 17). Mirrors the old
// ChartCard.svelte, which registered at its own module scope too.
ChartJS.register(
  CategoryScale,
  LinearScale,
  BarElement,
  LineElement,
  PointElement,
  Title,
  Tooltip,
  Legend,
);

interface ChartCardProps {
  title: string;
  type: "bar" | "line" | "horizontalBar";
  data: ChartJsData;
  options?: Record<string, unknown>;
  csv: string;
  suppressions?: Record<string, Record<number, string>>;
  filename?: string;
  noNeighborData?: boolean;
}

export function ChartCard({
  title,
  type,
  data,
  options = {},
  csv,
  suppressions = {},
  filename = "data",
  noNeighborData = false,
}: ChartCardProps) {
  const { locale: localeParam } = useParams<{ locale: string }>();
  const locale: Locale = availableLocales.includes(localeParam as Locale)
    ? (localeParam as Locale)
    : "en";
  const i18n = useMemo(() => createI18n(locale), [locale]);

  const [showTable, setShowTable] = useState(true);

  const hasData = data.datasets.length > 0;
  const hasSuppressions = Object.keys(suppressions).length > 0;

  const chartOptions = {
    responsive: true,
    maintainAspectRatio: false,
    indexAxis: type === "horizontalBar" ? ("y" as const) : ("x" as const),
    ...options,
  };

  const handleDownload = () => {
    downloadCSV(`${filename}.csv`, csv);
  };

  return (
    <div className="card space-y-4">
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <h3 className="font-semibold text-gray-800">{title}</h3>
        <div className="flex items-center gap-2 shrink-0">
          {hasData && (
            <button
              className="btn-secondary btn-sm"
              onClick={() => setShowTable((v) => !v)}
              aria-pressed={showTable}
            >
              {showTable
                ? i18n.t("chart.hideTable")
                : i18n.t("chart.showTable")}
            </button>
          )}
          {csv && (
            <button
              className="btn-secondary btn-sm"
              onClick={handleDownload}
              title={i18n.t("chart.downloadCsv")}
            >
              ↓ CSV
            </button>
          )}
        </div>
      </div>

      {/* Chart */}
      {hasData ? (
        <div className="relative h-64">
          {type === "line" ? (
            <Line data={data} options={chartOptions} />
          ) : (
            <Bar data={data} options={chartOptions} />
          )}
        </div>
      ) : (
        <div className="flex items-center justify-center h-32 bg-gray-50 rounded-lg text-gray-400 text-sm flex-col gap-2">
          <p>{i18n.t("chart.noData")}</p>
          {hasSuppressions && (
            <p className="text-xs text-amber-600 max-w-md text-center">
              {i18n.t("chart.suppressionNotice")}
            </p>
          )}
        </div>
      )}
      {hasData && noNeighborData && (
        <p className="text-xs text-gray-500 mt-2">
          Note: No neighbour data is available for this query.
        </p>
      )}

      {/* Suppression notice (only shown when data is available) */}
      {hasSuppressions && hasData && (
        <p className="text-xs text-amber-600">
          {i18n.t("chart.suppressionNotice")}
        </p>
      )}

      {/* Table */}
      {showTable && csv && <DataTable csv={csv} suppressions={suppressions} />}
    </div>
  );
}
