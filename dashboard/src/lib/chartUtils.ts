import type { PointStyle } from "chart.js";
import type { NewQueryResponse } from "./api";
import { parseCSV } from "./csvUtils";

const PALETTE = [
  "#3B82F6",
  "#10B981",
  "#F59E0B",
  "#EF4444",
  "#8B5CF6",
  "#06B6D4",
  "#F97316",
  "#84CC16",
  "#EC4899",
  "#6366F1",
];

const GREY = "#9CA3AF";

// Group styles for faceted line charts. Colour encodes the variable, so groups
// get a dash + point shape pair instead.
// ponytail: cycles after 5 groups; add patterns if larger groupings get used.
const DASHES = [[], [6, 4], [2, 3], [10, 4, 2, 4], [1, 6]];
const POINTS: PointStyle[] = [
  "circle",
  "triangle",
  "rect",
  "rectRot",
  "crossRot",
];

export interface ChartDataset {
  label: string;
  data: (number | null)[];
  backgroundColor: string;
  borderColor: string;
  borderWidth: number;
  tension?: number;
  fill?: boolean;
  borderDash?: number[];
  pointStyle?: PointStyle;
}

export interface ChartJsData {
  labels: string[];
  datasets: ChartDataset[];
}

export interface ChartOutput {
  data: ChartJsData;
  options: Record<string, unknown>;
  type?: "bar" | "line" | "horizontalBar";
}

export interface ChartLabelOptions {
  columnLabel?: (column: string) => string;
  countLabel?: string;
  meanLabel?: string;
  periodLabel?: string;
  min?: number;
  max?: number;
}

/**
 * Determine if data represents single-wave data (should use horizontal bar)
 * vs multi-wave data (should use line chart).
 */
function isSingleWave(groupBy: string[]): boolean {
  return !groupBy.includes("wave");
}

function resolveChartLabels(options: ChartLabelOptions = {}) {
  return {
    columnLabel: options.columnLabel ?? ((column: string) => column),
    countLabel: options.countLabel ?? "Count",
    meanLabel: options.meanLabel ?? "Mean",
    periodLabel: options.periodLabel ?? "Period",
  };
}

function baseOptions(
  yLabel = "Count",
  horizontal = false,
  min?: number,
  max?: number,
): Record<string, unknown> {
  const scaleConfig: Record<string, unknown> = {
    beginAtZero: true,
    title: { display: true, text: yLabel },
  };

  // Add min/max if provided
  if (min !== undefined) {
    scaleConfig.min = min;
  }
  if (max !== undefined) {
    scaleConfig.max = max;
  }

  return {
    responsive: true,
    maintainAspectRatio: false,
    indexAxis: horizontal ? "y" : "x",
    plugins: {
      legend: { position: "top" as const },
      tooltip: { mode: "index" as const, intersect: false },
    },
    scales: horizontal
      ? {
          x: scaleConfig,
        }
      : {
          y: scaleConfig,
        },
  };
}

/**
 * Convert a FrequencyResult to a bar-chart dataset.
 * If groupBy has one column: simple bar chart with that column as labels.
 * If groupBy has two columns: grouped bar chart (first col = x-axis, second = series).
 * Single-wave data uses horizontal bars for compactness.
 */
export function frequencyToChartData(
  result: { csv: string },
  groupBy: string[],
  labelOptions: ChartLabelOptions = {},
): ChartOutput {
  const chartLabels = resolveChartLabels(labelOptions);
  const { headers, rows } = parseCSV(result.csv);
  const horizontal = isSingleWave(groupBy);

  if (headers.length === 0 || rows.length === 0) {
    return {
      data: { labels: [], datasets: [] },
      options: baseOptions(
        chartLabels.countLabel,
        horizontal,
        labelOptions.min,
        labelOptions.max,
      ),
      type: horizontal ? "horizontalBar" : "bar",
    };
  }

  // Single group-by: labels = first col values, dataset = "n" or last numeric col
  if (groupBy.length <= 1) {
    const valueCol = headers[headers.length - 1];
    const xLabels = rows.map((r) => String(r[0] ?? ""));
    const data = rows.map((r) => {
      const v = r[headers.indexOf(valueCol)];
      return v === "" || v === undefined ? null : Number(v);
    });
    return {
      data: {
        labels: xLabels,
        datasets: [
          {
            label:
              valueCol === "n" || valueCol === "student_n"
                ? chartLabels.countLabel
                : chartLabels.columnLabel(valueCol),
            data,
            backgroundColor: PALETTE[0] + "CC",
            borderColor: PALETTE[0],
            borderWidth: 1,
          },
        ],
      },
      options: baseOptions(
        valueCol === "n" || valueCol === "student_n"
          ? chartLabels.countLabel
          : chartLabels.columnLabel(valueCol),
        horizontal,
        labelOptions.min,
        labelOptions.max,
      ),
      type: horizontal ? "horizontalBar" : "bar",
    };
  }

  // Two group-by columns: pivot on second column → grouped bars
  const [firstCol, secondCol] = groupBy;
  const firstIdx = headers.indexOf(firstCol);
  const secondIdx = headers.indexOf(secondCol);

  // All unique values for x-axis (first col) and series (second col)
  const xLabels = [...new Set(rows.map((r) => String(r[firstIdx] ?? "")))];
  const seriesLabels = [
    ...new Set(rows.map((r) => String(r[secondIdx] ?? ""))),
  ];

  // Value column = last column not in groupBy
  const valColIdx = headers.length - 1;

  const datasets: ChartDataset[] = seriesLabels.map((series, si) => {
    const data = xLabels.map((xLabel) => {
      const row = rows.find(
        (r) =>
          String(r[firstIdx]) === xLabel && String(r[secondIdx]) === series,
      );
      if (!row) return null;
      const v = row[valColIdx];
      return v === "" || v === undefined ? null : Number(v);
    });
    return {
      label: series,
      data,
      backgroundColor: PALETTE[si % PALETTE.length] + "CC",
      borderColor: PALETTE[si % PALETTE.length],
      borderWidth: 1,
    };
  });

  return {
    data: { labels: xLabels, datasets },
    options: baseOptions(
      chartLabels.countLabel,
      horizontal,
      labelOptions.min,
      labelOptions.max,
    ),
    type: horizontal ? "horizontalBar" : "bar",
  };
}

/**
 * Convert a FrequencyResult to a line-chart dataset (for trend/wave data).
 * xCol = the column to use as the x-axis (e.g. "wave").
 *
 * Multi-wave line chart styling:
 * - Wave on horizontal axis
 * - Non-focus school data in grey with alpha
 * - Focus school data in color with full opacity
 * - Focus school data split into different colored lines if required by aggregator
 *
 * Options:
 * - focusSchoolName: If provided, this school's data will be rendered in color with full opacity
 * - All other schools will be rendered in grey with alpha
 */
export function frequencyToLineData(
  result: { csv: string },
  groupBy: string[],
  xCol: string,
  labelOptions: ChartLabelOptions = {},
  focusSchoolName?: string,
): ChartOutput {
  const chartLabels = resolveChartLabels(labelOptions);
  const { headers, rows } = parseCSV(result.csv);

  if (headers.length === 0 || rows.length === 0) {
    const yScaleConfig: Record<string, unknown> = { beginAtZero: true };
    if (labelOptions.min !== undefined) yScaleConfig.min = labelOptions.min;
    if (labelOptions.max !== undefined) yScaleConfig.max = labelOptions.max;

    return {
      data: { labels: [], datasets: [] },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { position: "top" as const },
        },
        scales: {
          x: { title: { display: true, text: chartLabels.columnLabel(xCol) } },
          y: yScaleConfig,
        },
      },
      type: "line",
    };
  }

  // If we have 'school' in groupBy, we need to separate focus school from neighbors
  const hasSchool = headers.includes("school");
  const schoolIdx = hasSchool ? headers.indexOf("school") : -1;

  // Get all unique x-axis values (e.g., waves)
  const xIdx = headers.indexOf(xCol);
  const xLabels = [...new Set(rows.map((r) => String(r[xIdx] ?? "")))].sort();

  // Determine other grouping columns (exclude xCol and school)
  const otherGroupCols = groupBy.filter(
    (col) => col !== xCol && col !== "school",
  );

  if (hasSchool && focusSchoolName) {
    // Multi-school line chart with focus school styling
    const schools = [...new Set(rows.map((r) => String(r[schoolIdx] ?? "")))];

    // If there are other aggregations, split focus school data by them
    if (otherGroupCols.length > 0) {
      const datasets: ChartDataset[] = [];

      for (const school of schools) {
        const isFocus = school === focusSchoolName;
        const schoolRows = rows.filter((r) => String(r[schoolIdx]) === school);

        // Get unique values for other group columns
        const otherGroups = [
          ...new Set(
            schoolRows.map((r) =>
              otherGroupCols
                .map((col) => String(r[headers.indexOf(col)] ?? ""))
                .join(" / "),
            ),
          ),
        ];

        otherGroups.forEach((groupLabel, groupIdx) => {
          const data = xLabels.map((xLabel) => {
            const row = schoolRows.find((r) => {
              const xMatch = String(r[xIdx]) === xLabel;
              const groupMatch =
                otherGroupCols
                  .map((col) => String(r[headers.indexOf(col)] ?? ""))
                  .join(" / ") === groupLabel;
              return xMatch && groupMatch;
            });
            if (!row) return null;
            const v = row[headers.length - 1]; // Last column is the value
            return v === "" || v === undefined ? null : Number(v);
          });

          const label = isFocus
            ? otherGroupCols.length > 0
              ? groupLabel
              : school
            : school;

          datasets.push({
            label,
            data,
            tension: 0.3,
            fill: false,
            backgroundColor: isFocus
              ? PALETTE[groupIdx % PALETTE.length]
              : GREY,
            borderColor: isFocus
              ? PALETTE[groupIdx % PALETTE.length]
              : GREY + "80",
            borderWidth: 2,
          });
        });
      }

      const yScaleConfig: Record<string, unknown> = {
        beginAtZero: true,
        title: { display: true, text: chartLabels.meanLabel },
      };
      if (labelOptions.min !== undefined) yScaleConfig.min = labelOptions.min;
      if (labelOptions.max !== undefined) yScaleConfig.max = labelOptions.max;

      return {
        data: { labels: xLabels, datasets },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: {
            legend: { position: "top" as const },
          },
          scales: {
            x: {
              title: { display: true, text: chartLabels.columnLabel(xCol) },
            },
            y: yScaleConfig,
          },
        },
        type: "line",
      };
    } else {
      // Just school + wave, no other aggregations
      const datasets: ChartDataset[] = schools.map((school) => {
        const isFocus = school === focusSchoolName;
        const data = xLabels.map((xLabel) => {
          const row = rows.find(
            (r) =>
              String(r[schoolIdx]) === school && String(r[xIdx]) === xLabel,
          );
          if (!row) return null;
          const v = row[headers.length - 1];
          return v === "" || v === undefined ? null : Number(v);
        });

        return {
          label: school,
          data,
          tension: 0.3,
          fill: false,
          backgroundColor: isFocus ? PALETTE[0] : GREY,
          borderColor: isFocus ? PALETTE[0] : GREY + "80",
          borderWidth: 2,
        };
      });

      const yScaleConfig: Record<string, unknown> = {
        beginAtZero: true,
        title: { display: true, text: chartLabels.meanLabel },
      };
      if (labelOptions.min !== undefined) yScaleConfig.min = labelOptions.min;
      if (labelOptions.max !== undefined) yScaleConfig.max = labelOptions.max;

      return {
        data: { labels: xLabels, datasets },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: {
            legend: { position: "top" as const },
          },
          scales: {
            x: {
              title: { display: true, text: chartLabels.columnLabel(xCol) },
            },
            y: yScaleConfig,
          },
        },
        type: "line",
      };
    }
  } else {
    // Single school or no focus school specified - use base implementation
    const base = frequencyToChartData(result, groupBy, labelOptions);
    // Convert datasets to line style
    const datasets = base.data.datasets.map((ds, i) => ({
      ...ds,
      tension: 0.3,
      fill: false,
      backgroundColor: PALETTE[i % PALETTE.length],
      borderColor: PALETTE[i % PALETTE.length],
      borderWidth: 2,
    }));
    const options = {
      ...base.options,
      scales: {
        ...(base.options.scales as Record<string, unknown>),
        x: { title: { display: true, text: chartLabels.columnLabel(xCol) } },
      },
    };
    return { data: { ...base.data, datasets }, options, type: "line" };
  }
}

/**
 * Convert a means-like result to a bar-chart dataset.
 * Single-wave data uses horizontal bars for compactness.
 */
export function meansToChartData(
  result: { csv: string },
  groupBy: string[],
  labelOptions: ChartLabelOptions = {},
): ChartOutput {
  const chartLabels = resolveChartLabels(labelOptions);
  const { headers, rows } = parseCSV(result.csv);
  const horizontal = isSingleWave(groupBy);

  if (headers.length === 0 || rows.length === 0) {
    return {
      data: { labels: [], datasets: [] },
      options: baseOptions(
        chartLabels.meanLabel,
        horizontal,
        labelOptions.min,
        labelOptions.max,
      ),
      type: horizontal ? "horizontalBar" : "bar",
    };
  }

  // First column(s) are group-by, rest are value columns
  const groupCount = groupBy.length;
  const valueHeaders = headers.slice(groupCount);
  const rowLabels = rows.map((r) =>
    groupBy.map((_, gi) => String(r[gi] ?? "")).join(" / "),
  );

  const datasets: ChartDataset[] = valueHeaders.map((vh, vi) => {
    const data = rows.map((r) => {
      const v = r[groupCount + vi];
      return v === "" || v === undefined ? null : Number(v);
    });
    return {
      label: chartLabels.columnLabel(vh),
      data,
      backgroundColor: PALETTE[vi % PALETTE.length] + "CC",
      borderColor: PALETTE[vi % PALETTE.length],
      borderWidth: 1,
    };
  });

  return {
    data: { labels: rowLabels, datasets },
    options: baseOptions(
      chartLabels.meanLabel,
      horizontal,
      labelOptions.min,
      labelOptions.max,
    ),
    type: horizontal ? "horizontalBar" : "bar",
  };
}

export function queryToChartData(
  result: { csv: string },
  groupBy: string[],
  labelOptions: ChartLabelOptions = {},
): ChartOutput {
  return meansToChartData(result, groupBy, labelOptions);
}

/**
 * Convert QueryResponse to chart data for wave analysis
 * Handles focus school vs neighbor styling:
 * - Non-focus school data in grey with alpha
 * - Focus school data in colour with full opacity
 * - Focus school data split into different colored lines if required by aggregator
 */
export function queryToWaveChart(
  focusSchool: {
    school_name: string;
    results: Record<string, unknown>[] | null;
  },
  neighbors: {
    school_name: string;
    results: Record<string, unknown>[] | null;
  }[],
  aggregations: string[],
  labelOptions: ChartLabelOptions = {},
): ChartOutput {
  const chartLabels = resolveChartLabels(labelOptions);

  // Combine all data into CSV format for processing
  if (!focusSchool.results || focusSchool.results.length === 0) {
    const yScaleConfig: Record<string, unknown> = {
      beginAtZero: true,
      title: { display: true, text: chartLabels.meanLabel },
    };
    if (labelOptions.min !== undefined) yScaleConfig.min = labelOptions.min;
    if (labelOptions.max !== undefined) yScaleConfig.max = labelOptions.max;

    return {
      data: { labels: [], datasets: [] },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { position: "top" as const } },
        scales: {
          x: {
            title: { display: true, text: chartLabels.columnLabel("wave") },
          },
          y: yScaleConfig,
        },
      },
      type: "line",
    };
  }

  const allResults: Array<
    Record<string, unknown> & { school: string; _isFocus: boolean }
  > = [
    ...focusSchool.results.map((r) => ({
      ...r,
      school: focusSchool.school_name,
      _isFocus: true,
    })),
    ...neighbors.flatMap((n) =>
      (n.results || []).map((r) => ({
        ...r,
        school: n.school_name,
        _isFocus: false,
      })),
    ),
  ];

  // Convert to CSV
  const headers = ["school", ...aggregations, "mean", "student_n"];
  const csvRows = allResults.map((r) => {
    return headers
      .map((h) => {
        if (h === "school") return String(r.school);
        return String(r[h] ?? "");
      })
      .join(",");
  });
  const csv = headers.join(",") + "\n" + csvRows.join("\n");

  // Use frequencyToLineData with focus school name
  return frequencyToLineData(
    { csv },
    ["school", ...aggregations],
    "wave",
    labelOptions,
    focusSchool.school_name,
  );
}

/**
 * Convert NewQueryResponse (period-based multi-variable) to ChartJsData
 *
 * For single variable:
 * - Multiple periods: line chart with periods on x-axis
 * - Single period: bar chart with dimension values on x-axis
 *
 * For multiple variables:
 * - Multiple periods: line chart with one line per variable
 * - Single period: bar chart with one bar per variable
 *
 * Handles suppression and rescaling notes appropriately
 */
export function newQueryToChartData(
  response: NewQueryResponse,
  labelOptions: ChartLabelOptions = {},
): ChartOutput {
  const chartLabels = resolveChartLabels(labelOptions);
  const { variables, periods, dimensions } = response;

  if (variables.length === 0 || periods.length === 0) {
    return {
      data: { labels: [], datasets: [] },
      options: baseOptions(
        chartLabels.meanLabel,
        false,
        labelOptions.min,
        labelOptions.max,
      ),
      type: "bar",
    };
  }

  // Multi-period: line chart
  if (periods.length > 1) {
    const datasets: ChartDataset[] = [];

    variables.forEach((variableSlice, varIdx) => {
      // For each variable, create a line across periods
      const data: (number | null)[] = periods.map((periodId) => {
        const periodSlice = variableSlice.periods[periodId];
        if (
          !periodSlice ||
          periodSlice.suppressed ||
          !periodSlice.cells ||
          periodSlice.cells.length === 0
        ) {
          return null;
        }
        // If no dimensions, take the single cell's mean
        // If dimensions exist, we'd need to aggregate - for now, take first cell
        const mean = periodSlice.cells[0].mean;
        return mean !== undefined ? mean : null;
      });

      datasets.push({
        label: chartLabels.columnLabel(variableSlice.variable),
        data,
        tension: 0.3,
        fill: false,
        backgroundColor: PALETTE[varIdx % PALETTE.length],
        borderColor: PALETTE[varIdx % PALETTE.length],
        borderWidth: 2,
      });
    });

    const yScaleConfig: Record<string, unknown> = {
      beginAtZero: true,
      title: { display: true, text: chartLabels.meanLabel },
    };
    if (labelOptions.min !== undefined) yScaleConfig.min = labelOptions.min;
    if (labelOptions.max !== undefined) yScaleConfig.max = labelOptions.max;

    return {
      data: { labels: periods, datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { position: "top" as const },
        },
        scales: {
          x: { title: { display: true, text: chartLabels.periodLabel } },
          y: yScaleConfig,
        },
      },
      type: "line",
    };
  } else {
    // Single period: horizontal bar chart
    const periodId = periods[0];
    const labels: string[] = [];
    const data: (number | null)[] = [];

    variables.forEach((variableSlice) => {
      const periodSlice = variableSlice.periods[periodId];
      if (
        !periodSlice ||
        periodSlice.suppressed ||
        !periodSlice.cells ||
        periodSlice.cells.length === 0
      ) {
        labels.push(chartLabels.columnLabel(variableSlice.variable));
        data.push(null);
      } else {
        // If no dimensions, single value per variable
        if (dimensions.length === 0) {
          labels.push(chartLabels.columnLabel(variableSlice.variable));
          const mean = periodSlice.cells[0].mean;
          data.push(mean !== undefined ? mean : null);
        } else {
          // With dimensions, each cell represents a coordinate
          // For simplicity, show variable + first dimension value
          periodSlice.cells.forEach((cell) => {
            const dimLabel = dimensions
              .map((d) => `${d}=${cell[d]}`)
              .join(", ");
            labels.push(
              `${chartLabels.columnLabel(variableSlice.variable)} (${dimLabel})`,
            );
            const mean = cell.mean;
            data.push(mean !== undefined ? mean : null);
          });
        }
      }
    });

    return {
      data: {
        labels,
        datasets: [
          {
            label: chartLabels.meanLabel,
            data,
            backgroundColor: PALETTE[0] + "CC",
            borderColor: PALETTE[0],
            borderWidth: 1,
          },
        ],
      },
      options: baseOptions(
        chartLabels.meanLabel,
        true,
        labelOptions.min,
        labelOptions.max,
      ),
      type: "horizontalBar",
    };
  }
}

export type FacetMode = "variable" | "group";

export interface ChartFacet {
  title: string;
  data: ChartJsData;
}

/**
 * Split a multi-period, dimensioned NewQueryResponse into small-multiple line
 * charts: one per variable (lines = groups) or one per group (lines =
 * variables). Colour always tracks the variable and dash/point the group, so
 * toggling the mode never changes what a line looks like. All facets share a
 * y-axis ceiling so they can be compared by eye.
 */
export function newQueryToFacets(
  response: NewQueryResponse,
  mode: FacetMode,
  labelOptions: ChartLabelOptions = {},
): { facets: ChartFacet[]; options: Record<string, unknown> } {
  const chartLabels = resolveChartLabels(labelOptions);
  const { variables, periods, dimensions } = response;

  // Group label -> mean per variable per period, in first-seen order
  const groups = new Map<string, (number | null)[][]>();
  let max = 0;
  variables.forEach((v, vi) =>
    periods.forEach((periodId, pi) => {
      const slice = v.periods[periodId];
      if (!slice || slice.suppressed) return;
      for (const cell of slice.cells ?? []) {
        const label = dimensions
          .map((d) => `${chartLabels.columnLabel(d)}: ${cell[d]}`)
          .join(", ");
        if (!groups.has(label))
          groups.set(
            label,
            variables.map(() => periods.map(() => null)),
          );
        const mean = cell.mean ?? null;
        groups.get(label)![vi][pi] = mean;
        if (mean !== null) max = Math.max(max, mean);
      }
    }),
  );

  const groupLabels = [...groups.keys()];
  const line = (vi: number, gi: number, label: string): ChartDataset => ({
    label,
    data: groups.get(groupLabels[gi])![vi],
    tension: 0.3,
    fill: false,
    backgroundColor: PALETTE[vi % PALETTE.length],
    borderColor: PALETTE[vi % PALETTE.length],
    borderWidth: 2,
    borderDash: DASHES[gi % DASHES.length],
    pointStyle: POINTS[gi % POINTS.length],
  });
  const variableLabels = variables.map((v) =>
    chartLabels.columnLabel(v.variable),
  );

  const facets =
    mode === "variable"
      ? variableLabels.map((title, vi) => ({
          title,
          data: {
            labels: periods,
            datasets: groupLabels.map((g, gi) => line(vi, gi, g)),
          },
        }))
      : groupLabels.map((title, gi) => ({
          title,
          data: {
            labels: periods,
            datasets: variableLabels.map((v, vi) => line(vi, gi, v)),
          },
        }));

  const y: Record<string, unknown> = {
    beginAtZero: true,
    suggestedMax: max,
    title: { display: true, text: chartLabels.meanLabel },
  };
  if (labelOptions.min !== undefined) y.min = labelOptions.min;
  if (labelOptions.max !== undefined) y.max = labelOptions.max;

  return {
    facets,
    options: {
      plugins: {
        legend: { position: "top" as const, labels: { usePointStyle: true } },
      },
      scales: {
        x: { title: { display: true, text: chartLabels.periodLabel } },
        y,
      },
    },
  };
}

/**
 * Convert NewQueryResponse to CSV format
 *
 * CSV structure:
 * Variable, Period, [Dimension1, Dimension2, ...], Mean, N
 */
export function newQueryToCSV(response: NewQueryResponse): string {
  return newQueryToCSVWithLabels(response);
}

export function newQueryToCSVWithLabels(
  response: NewQueryResponse,
  labelOptions: ChartLabelOptions = {},
): string {
  const chartLabels = resolveChartLabels(labelOptions);
  const { variables, periods, dimensions } = response;

  // Build header
  const headers = ["Variable", "Period", ...dimensions, "Mean", "N"];
  const rows = [headers.join(",")];

  // Add data rows
  variables.forEach((variableSlice) => {
    periods.forEach((periodId) => {
      const periodSlice = variableSlice.periods[periodId];

      if (!periodSlice || periodSlice.suppressed || !periodSlice.cells) {
        // Suppressed period - add a row with empty values
        const csvRow = [
          chartLabels.columnLabel(variableSlice.variable),
          periodId,
          ...dimensions.map(() => ""),
          "SUPPRESSED",
          "0",
        ];
        rows.push(csvRow.join(","));
      } else {
        // Add row for each cell
        periodSlice.cells.forEach((cell) => {
          const dimValues = dimensions.map((d) => String(cell[d] ?? ""));
          const mean = cell.mean !== undefined ? cell.mean.toFixed(2) : "";
          const n = String(cell.n);

          const csvRow = [
            chartLabels.columnLabel(variableSlice.variable),
            periodId,
            ...dimValues,
            mean,
            n,
          ];
          rows.push(csvRow.join(","));
        });
      }
    });
  });

  return rows.join("\n");
}
