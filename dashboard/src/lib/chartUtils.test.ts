import { describe, it, expect } from "vitest";
import { newQueryToFacets } from "./chartUtils";
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

  it("styles colour by variable and dash by group in both modes", () => {
    const byVar = newQueryToFacets(response, "variable", labels).facets;
    const byGroup = newQueryToFacets(response, "group", labels).facets;
    // variable 2, group F
    const a = byVar[1].data.datasets[1];
    const b = byGroup[1].data.datasets[1];
    expect(a.borderColor).toBe(b.borderColor);
    expect(a.borderDash).toEqual(b.borderDash);
    expect(a.pointStyle).toBe(b.pointStyle);
    // groups differ in dash, variables differ in colour
    expect(byVar[0].data.datasets[0].borderDash).not.toEqual(
      byVar[0].data.datasets[1].borderDash,
    );
    expect(byGroup[0].data.datasets[0].borderColor).not.toBe(
      byGroup[0].data.datasets[1].borderColor,
    );
  });

  it("shares one y-axis ceiling across facets", () => {
    const { options } = newQueryToFacets(response, "group", labels);
    const y = (options.scales as Record<string, Record<string, unknown>>).y;
    expect(y.suggestedMax).toBe(3.9);
  });
});
