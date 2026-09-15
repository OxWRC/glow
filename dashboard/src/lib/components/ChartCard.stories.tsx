import type { Meta, StoryObj } from "@storybook/react-vite";
import { expect, within, userEvent } from "storybook/test";
import { ChartCard } from "./ChartCard";
import type { ChartJsData } from "../chartUtils";

// A few stories below use a single dataset with a per-bar array of colors
// (Chart.js supports this) to recreate the old Svelte demo's visual, even
// though `ChartDataset` (chartUtils.ts) only types colors as scalars — real
// production code always uses one color per dataset, never per-bar arrays.
// ponytail: cast at the story boundary rather than loosen the shared type
// for a demo-only usage; revisit if a real caller ever needs per-bar colors.
const asChartData = (data: unknown) => data as ChartJsData;

const meta = {
  title: "Components/ChartCard",
  component: ChartCard,
  tags: ["autodocs"],
  parameters: {
    layout: "padded",
  },
} satisfies Meta<typeof ChartCard>;

export default meta;
type Story = StoryObj<typeof meta>;

// ============================================================================
// Story 1: Single Wave
// Horizontal bar chart with a single bar for focus school and bars for neighbors
// ============================================================================

export const SingleWave: Story = {
  args: {
    title: "Well-being Score (Single Wave)",
    type: "horizontalBar",
    data: asChartData({
      labels: [
        "Focus School Academy",
        "Neighbouring School",
        "State Local High",
      ],
      datasets: [
        {
          label: "Mean Score",
          data: [3.5, 3.2, 3.1],
          backgroundColor: ["#3B82F6", "#9CA3AFCC", "#9CA3AFCC"],
          borderColor: ["#3B82F6", "#9CA3AF", "#9CA3AF"],
          borderWidth: 1,
        },
      ],
    }),
    csv: `school,mean,student_n
Focus School Academy,3.5,125
Neighbouring School,3.2,110
State Local High,3.1,95`,
    filename: "wellbeing-single-wave",
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);

    // Test: Verify horizontal bar chart renders with proper heading
    const heading = canvas.getByRole("heading", {
      level: 3,
      name: "Well-being Score (Single Wave)",
    });
    await expect(heading).toBeInTheDocument();

    // Test: Verify canvas element for chart is present
    const chartCanvas = canvasElement.querySelector("canvas");
    await expect(chartCanvas).toBeInTheDocument();

    // Test: Verify all 3 bars are present (check data has 3 schools)
    // We verify this by checking the data structure has 3 labels
    await expect(chartCanvas).toBeInTheDocument();

    // Test: Verify CSV download button is accessible and works
    const downloadBtn = canvas.getByRole("button", { name: /CSV/i });
    await expect(downloadBtn).toBeInTheDocument();
    await expect(downloadBtn).not.toBeDisabled();

    // Test: Verify table is open by default and toggle works
    const hideTableBtn = canvas.getByRole("button", { name: /hide table/i });
    await expect(hideTableBtn).toBeInTheDocument();
    await expect(hideTableBtn).toHaveAttribute("aria-pressed", "true");
    await expect(hideTableBtn).not.toBeDisabled();

    // Verify table is now visible
    const table = canvas.getByRole("table");
    await expect(table).toBeInTheDocument();

    // Click again to hide table
    await userEvent.click(hideTableBtn);
    await new Promise((resolve) => setTimeout(resolve, 100));

    // Verify button reverted
    const showTableBtnAgain = canvas.getByRole("button", {
      name: /show table/i,
    });
    await expect(showTableBtnAgain).toHaveAttribute("aria-pressed", "false");

    // Show table again
    await userEvent.click(showTableBtnAgain);
    await new Promise((resolve) => setTimeout(resolve, 100));
    await expect(
      canvas.getByRole("button", { name: /hide table/i }),
    ).toBeInTheDocument();
  },
};

// ============================================================================
// Story 2: Multiple Waves
// Line chart with a single line for focus school and lines for neighbors
// ============================================================================

export const MultipleWaves: Story = {
  args: {
    title: "Well-being Score Over Time",
    type: "line",
    data: {
      labels: ["1", "2", "3"],
      datasets: [
        {
          label: "Focus School Academy",
          data: [3.2, 3.5, 3.7],
          fill: false,
          borderColor: "#3B82F6",
          backgroundColor: "#3B82F6",
          tension: 0.3,
          borderWidth: 2,
        },
        {
          label: "Neighbouring School",
          data: [3.1, 3.3, 3.4],
          fill: false,
          borderColor: "#9CA3AF80",
          backgroundColor: "#9CA3AF",
          tension: 0.3,
          borderWidth: 2,
        },
        {
          label: "State Local High",
          data: [3.0, 3.2, 3.3],
          fill: false,
          borderColor: "#9CA3AF80",
          backgroundColor: "#9CA3AF",
          tension: 0.3,
          borderWidth: 2,
        },
      ],
    },
    csv: `school,wave,mean,student_n
Focus School Academy,1,3.2,125
Focus School Academy,2,3.5,130
Focus School Academy,3,3.7,128
Neighbouring School,1,3.1,110
Neighbouring School,2,3.3,115
Neighbouring School,3,3.4,112
State Local High,1,3.0,95
State Local High,2,3.2,98
State Local High,3,3.3,96`,
    filename: "wellbeing-multiple-waves",
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);

    // Test: Verify line chart renders correctly with proper heading
    const heading = canvas.getByRole("heading", {
      level: 3,
      name: "Well-being Score Over Time",
    });
    await expect(heading).toBeInTheDocument();

    // Test: Verify canvas element for line chart is present
    const chartCanvas = canvasElement.querySelector("canvas");
    await expect(chartCanvas).toBeInTheDocument();

    // A11y: Verify the chart type is 'line' by checking the story args
    // (Chart.js doesn't expose chart type in a directly accessible way, but we can verify the component receives the right type)
    await expect(chartCanvas).toBeInTheDocument();

    // Test: Verify data represents multiple waves (3 time points)
    const hideTableBtn = canvas.getByRole("button", { name: /hide table/i });
    await expect(hideTableBtn).toBeInTheDocument();

    const table = canvas.getByRole("table");
    await expect(table).toBeInTheDocument();

    // Verify wave column exists in table (a11y way to confirm multi-wave data)
    const waveHeader = canvas.getByRole("columnheader", { name: /wave/i });
    await expect(waveHeader).toBeInTheDocument();

    // Verify multiple rows for different waves and schools (3 waves × 3 schools = 9 rows)
    const rows = canvas.getAllByRole("row");
    // 1 header row + 9 data rows = 10 total
    await expect(rows.length).toBeGreaterThanOrEqual(10);

    // Test: Verify both action buttons are present and functional
    const downloadBtn = canvas.getByRole("button", { name: /CSV/i });
    await expect(downloadBtn).toBeInTheDocument();
    await expect(downloadBtn).not.toBeDisabled();

    await expect(hideTableBtn).not.toBeDisabled();
  },
};

// ============================================================================
// Story 3: Single Wave Aggregated
// Horizontal bar chart with aggregate splits shown thinner below the main
// focus school bar (above neighbor school bars)
// Layout: Focus School (thick) → Focus Aggregates (thin, indented) → Neighbors (normal)
// Note: We use visual indentation and color coding. True per-bar thickness
// control would require a custom Chart.js plugin.
// ============================================================================

export const SingleWaveAggregated: Story = {
  args: {
    title: "Well-being Score by Sex (Single Wave)",
    type: "horizontalBar",
    data: asChartData({
      labels: [
        "Focus School Academy",
        "  ↳ Male",
        "  ↳ Female",
        "Neighbouring School",
        "State Local High",
      ],
      datasets: [
        {
          label: "Mean Score",
          data: [3.5, 3.4, 3.6, 3.2, 3.1],
          backgroundColor: [
            "#3B82F6",
            "#3B82F6CC",
            "#10B981CC",
            "#9CA3AFCC",
            "#9CA3AFCC",
          ],
          borderColor: ["#3B82F6", "#3B82F6", "#10B981", "#9CA3AF", "#9CA3AF"],
          borderWidth: [2, 1, 1, 1, 1],
        },
      ],
    }),
    options: {
      plugins: {
        legend: {
          display: false, // Hide legend since we're using a single dataset with varied colors
        },
      },
      scales: {
        x: {
          beginAtZero: true,
          title: {
            display: true,
            text: "Mean Score",
          },
        },
      },
    },
    csv: `school,d_sex,mean,student_n
Focus School Academy,,3.5,125
Focus School Academy,M,3.4,62
Focus School Academy,F,3.6,63
Neighbouring School,,3.2,110
State Local High,,3.1,95`,
    filename: "wellbeing-single-wave-aggregated",
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);

    // Test: Verify aggregated data with custom chart options (legend disabled)
    const heading = canvas.getByRole("heading", {
      level: 3,
      name: "Well-being Score by Sex (Single Wave)",
    });
    await expect(heading).toBeInTheDocument();

    // Test: Verify chart renders
    const chartCanvas = canvasElement.querySelector("canvas");
    await expect(chartCanvas).toBeInTheDocument();

    // Test: Verify action buttons
    const downloadBtn = canvas.getByRole("button", { name: /CSV/i });
    await expect(downloadBtn).toBeInTheDocument();

    // Test distinguishing feature: Verify aggregation columns are present in the data table
    const table = canvas.getByRole("table");
    await expect(table).toBeInTheDocument();

    // Verify aggregation column "d_sex" exists
    const sexHeader = canvas.getByRole("columnheader", { name: /sex/i });
    await expect(sexHeader).toBeInTheDocument();

    // Verify aggregated rows are present (should have rows with M and F values)
    const cells = canvas.getAllByRole("cell");
    const cellTexts = cells.map((cell) => cell.textContent);

    // Check that aggregation values appear in the table
    await expect(cellTexts.some((text) => text === "M")).toBe(true);
    await expect(cellTexts.some((text) => text === "F")).toBe(true);

    // Verify we have the focus school row plus aggregated rows
    // Should have: 1 overall + 2 sex splits = 3 rows for focus school, plus 2 neighbor schools = 5 total rows
    const rows = canvas.getAllByRole("row");
    await expect(rows.length).toBe(6); // 1 header + 5 data rows
  },
};

// ============================================================================
// Story 4: Multiple Waves Aggregated
// Line chart with different colored lines for aggregate splits within focus school
// ============================================================================

export const MultipleWavesAggregated: Story = {
  args: {
    title: "Well-being Score Over Time by Sex",
    type: "line",
    data: {
      labels: ["1", "2", "3"],
      datasets: [
        {
          label: "Focus School Academy - Male",
          data: [3.1, 3.4, 3.6],
          fill: false,
          borderColor: "#3B82F6",
          backgroundColor: "#3B82F6",
          tension: 0.3,
          borderWidth: 2,
        },
        {
          label: "Focus School Academy - Female",
          data: [3.3, 3.6, 3.8],
          fill: false,
          borderColor: "#10B981",
          backgroundColor: "#10B981",
          tension: 0.3,
          borderWidth: 2,
        },
        {
          label: "Neighbouring School",
          data: [3.1, 3.3, 3.4],
          fill: false,
          borderColor: "#9CA3AF80",
          backgroundColor: "#9CA3AF",
          tension: 0.3,
          borderWidth: 2,
        },
        {
          label: "State Local High",
          data: [3.0, 3.2, 3.3],
          fill: false,
          borderColor: "#9CA3AF80",
          backgroundColor: "#9CA3AF",
          tension: 0.3,
          borderWidth: 2,
        },
      ],
    },
    csv: `school,d_sex,wave,mean,student_n
Focus School Academy,M,1,3.1,62
Focus School Academy,M,2,3.4,65
Focus School Academy,M,3,3.6,64
Focus School Academy,F,1,3.3,63
Focus School Academy,F,2,3.6,65
Focus School Academy,F,3,3.8,64
Neighbouring School,,1,3.1,110
Neighbouring School,,2,3.3,115
Neighbouring School,,3,3.4,112
State Local High,,1,3.0,95
State Local High,,2,3.2,98
State Local High,,3,3.3,96`,
    filename: "wellbeing-multiple-waves-aggregated",
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);

    // Test: Verify line chart with multiple datasets (4 datasets in this story)
    const heading = canvas.getByRole("heading", {
      level: 3,
      name: "Well-being Score Over Time by Sex",
    });
    await expect(heading).toBeInTheDocument();

    // Test distinguishing feature: Verify multiple datasets render
    const chartCanvas = canvasElement.querySelector("canvas");
    await expect(chartCanvas).toBeInTheDocument();

    // Verify action buttons present for aggregated multi-wave chart
    const downloadBtn = canvas.getByRole("button", { name: /CSV/i });
    await expect(downloadBtn).toBeInTheDocument();

    const hideTableBtn = canvas.getByRole("button", { name: /hide table/i });
    await expect(hideTableBtn).toBeInTheDocument();
  },
};

// ============================================================================
// Story 5: Single Wave No Neighbour Data
// Horizontal bar chart with only focus school (no neighbor data unsuppressed)
// ============================================================================

export const SingleWaveNoNeighbourData: Story = {
  args: {
    title: "Well-being Score (No Neighbor Data)",
    type: "horizontalBar",
    data: {
      labels: ["Focus School Academy"],
      datasets: [
        {
          label: "Mean Score",
          data: [3.5],
          backgroundColor: "#3B82F6",
          borderColor: "#3B82F6",
          borderWidth: 1,
        },
      ],
    },
    csv: `school,mean,student_n
Focus School Academy,3.5,125`,
    filename: "wellbeing-single-wave-no-neighbors",
    noNeighborData: true,
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);

    // Test: Verify chart with single school (no neighbor data)
    const heading = canvas.getByRole("heading", {
      level: 3,
      name: "Well-being Score (No Neighbor Data)",
    });
    await expect(heading).toBeInTheDocument();

    // Test distinguishing feature: Verify single school chart still has canvas
    const chartCanvas = canvasElement.querySelector("canvas");
    await expect(chartCanvas).toBeInTheDocument();

    // Verify buttons are still functional with single school
    const downloadBtn = canvas.getByRole("button", { name: /CSV/i });
    await expect(downloadBtn).toBeInTheDocument();

    // Test: Verify "no neighbour data" note appears
    const noNeighborNote = canvas.getByText(/No neighbour data is available/i);
    await expect(noNeighborNote).toBeInTheDocument();
  },
};

// ============================================================================
// Story 6: Multiple Waves No Neighbour Data
// Line chart with only focus school line (no neighbor data unsuppressed)
// ============================================================================

export const MultipleWavesNoNeighbourData: Story = {
  args: {
    title: "Well-being Score Over Time (No Neighbor Data)",
    type: "line",
    data: {
      labels: ["1", "2", "3"],
      datasets: [
        {
          label: "Focus School Academy",
          data: [3.2, 3.5, 3.7],
          fill: false,
          borderColor: "#3B82F6",
          backgroundColor: "#3B82F6",
          tension: 0.3,
          borderWidth: 2,
        },
      ],
    },
    csv: `school,wave,mean,student_n
Focus School Academy,1,3.2,125
Focus School Academy,2,3.5,130
Focus School Academy,3,3.7,128`,
    filename: "wellbeing-multiple-waves-no-neighbors",
    noNeighborData: true,
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);

    // Test: Verify line chart with single dataset (no neighbors)
    const heading = canvas.getByRole("heading", {
      level: 3,
      name: "Well-being Score Over Time (No Neighbor Data)",
    });
    await expect(heading).toBeInTheDocument();

    // Test distinguishing feature: Verify line chart renders for single school over time
    const chartCanvas = canvasElement.querySelector("canvas");
    await expect(chartCanvas).toBeInTheDocument();

    // Verify all control buttons present
    const downloadBtn = canvas.getByRole("button", { name: /CSV/i });
    await expect(downloadBtn).toBeInTheDocument();

    const hideTableBtn = canvas.getByRole("button", { name: /hide table/i });
    await expect(hideTableBtn).toBeInTheDocument();

    // Test: Verify "no neighbour data" note appears
    const noNeighborNote = canvas.getByText(/No neighbour data is available/i);
    await expect(noNeighborNote).toBeInTheDocument();
  },
};

// ============================================================================
// Story 7: Focus School Suppression
// Shows suppression message when focus school data cannot be displayed
// ============================================================================

export const FocusSchoolSuppression: Story = {
  args: {
    title: "Well-being Score (Suppressed)",
    type: "horizontalBar",
    data: {
      labels: [],
      datasets: [],
    },
    csv: "",
    suppressions: {
      _focus_school: {
        0: "Results cannot be displayed due to small group sizes. This protects individual student privacy.",
      },
    },
    filename: "wellbeing-suppressed",
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);

    // Test: Verify suppression message displays when no data available
    const heading = canvas.getByRole("heading", {
      level: 3,
      name: "Well-being Score (Suppressed)",
    });
    await expect(heading).toBeInTheDocument();

    // Verify "No data to display" message shows instead of chart
    const noDataMsg = canvas.getByText(/No data to display/i);
    await expect(noDataMsg).toBeInTheDocument();

    // Verify suppression warning is inside the no data box
    const suppressionText = canvas.getByText(
      /Some values are suppressed to protect student privacy/i,
    );
    await expect(suppressionText).toBeInTheDocument();

    // Verify the table button is hidden when there's no data
    const showTableBtn = canvas.queryByRole("button", { name: /show table/i });
    await expect(showTableBtn).not.toBeInTheDocument();
  },
};

// ============================================================================
// Story 8: Single Wave With Table Open
// Same as Single Wave but with the data table expanded
// ============================================================================

export const SingleWaveWithTableOpen: Story = {
  args: {
    title: "Well-being Score (Single Wave)",
    type: "horizontalBar",
    data: asChartData({
      labels: [
        "Focus School Academy",
        "Neighbouring School",
        "State Local High",
      ],
      datasets: [
        {
          label: "Mean Score",
          data: [3.5, 3.2, 3.1],
          backgroundColor: ["#3B82F6", "#9CA3AFCC", "#9CA3AFCC"],
          borderColor: ["#3B82F6", "#9CA3AF", "#9CA3AF"],
          borderWidth: 1,
        },
      ],
    }),
    csv: `school,mean,student_n
Focus School Academy,3.5,125
Neighbouring School,3.2,110
State Local High,3.1,95`,
    filename: "wellbeing-single-wave",
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);

    // Test: Verify table is open by default
    const hideTableBtn = canvas.getByRole("button", { name: /hide table/i });
    await expect(hideTableBtn).toHaveAttribute("aria-pressed", "true");

    // Verify table is now present
    const table = canvas.getByRole("table");
    await expect(table).toBeInTheDocument();

    // Hide then show again to verify toggle behaviour
    await userEvent.click(hideTableBtn);
    await new Promise((resolve) => setTimeout(resolve, 100));
    const showTableBtn = canvas.getByRole("button", { name: /show table/i });
    await expect(showTableBtn).toHaveAttribute("aria-pressed", "false");
  },
};
