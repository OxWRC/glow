import { describe, it, expect } from "vitest";
import { newQueryToChartData, newQueryToFacets } from "./chartUtils";
import type { NewQueryResponse } from "./api";
import { getExample } from "./mocks/contractExamples";

const response = getExample("query.period-based.multi-variable-dimensions")
  ?.response as NewQueryResponse;
const labels = { columnLabel: (c: string) => c.split("__").pop() ?? c };

describe("newQueryToFacets", () => {
  it("draws one chart per variable with a line per group", () => {
    const { facets } = newQueryToFacets(response, "variable", labels);
    expect(facets.map((f) => f.title)).toEqual(["bw_wbeing_1", "bw_wbeing_2"]);
    expect(facets[0].data.labels).toEqual(["2023-2024", "2024-2025"]);
    expect(facets[0].data.datasets.map((d) => d.label)).toEqual([
      "d_sex: M",
      "d_sex: F",
    ]);
    expect(facets[0].data.datasets[1].data).toEqual([3.8, 3.9]);
    // Suppressed period is a gap, not a zero
    expect(facets[1].data.datasets[0].data).toEqual([2.9, null]);
  });

  it("draws one chart per group with a line per variable", () => {
    const { facets } = newQueryToFacets(response, "group", labels);
    expect(facets.map((f) => f.title)).toEqual(["d_sex: M", "d_sex: F"]);
    expect(facets[0].data.datasets.map((d) => d.label)).toEqual([
      "bw_wbeing_1",
      "bw_wbeing_2",
    ]);
    expect(facets[1].data.datasets[0].data).toEqual([3.8, 3.9]);
  });

  it("varies colour and dash between lines within each chart", () => {
    const byVar = newQueryToFacets(response, "variable", labels).facets;
    const byGroup = newQueryToFacets(response, "group", labels).facets;
    for (const facet of [...byVar, ...byGroup]) {
      const [a, b] = facet.data.datasets;
      expect(a.borderColor).not.toBe(b.borderColor);
      expect(a.borderDash).not.toEqual(b.borderDash);
    }
    // A group keeps its colour across the per-variable charts
    expect(byVar[0].data.datasets[1].borderColor).toBe(
      byVar[1].data.datasets[1].borderColor,
    );
  });

  it("shares one y-axis ceiling across facets", () => {
    const { options } = newQueryToFacets(response, "group", labels);
    const y = (options.scales as Record<string, Record<string, unknown>>).y;
    expect(y.suggestedMax).toBe(3.9);
  });
});

describe("newQueryToChartData, single period with a grouping", () => {
  const single = getExample("query.period-based.with-dimensions")
    ?.response as NewQueryResponse;

  it("draws a bar per variable with a coloured series per group", () => {
    const { data, type } = newQueryToChartData(single, labels);
    expect(type).toBe("horizontalBar");
    expect(data.labels).toEqual(["bw_wbeing_1"]);
    expect(data.datasets.map((d) => d.label)).toEqual(["d_sex: M", "d_sex: F"]);
    expect(data.datasets.map((d) => d.data)).toEqual([[3.2], [3.8]]);
    expect(data.datasets[0].borderColor).not.toBe(data.datasets[1].borderColor);
  });

  it("leaves a suppressed variable as a gap in every group", () => {
    const suppressed: NewQueryResponse = {
      ...single,
      variables: [
        ...single.variables,
        {
          variable: "x__other",
          periods: { "2023-2024": { suppressed: true } },
        },
      ],
    };
    const { data } = newQueryToChartData(suppressed, labels);
    expect(data.labels).toEqual(["bw_wbeing_1", "other"]);
    expect(data.datasets.map((d) => d.data)).toEqual([
      [3.2, null],
      [3.8, null],
    ]);
  });
});
