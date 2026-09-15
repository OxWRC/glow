import type { Meta, StoryObj } from "@storybook/react-vite";
import { expect, within, userEvent } from "storybook/test";
import { DataTable } from "./DataTable";

const meta = {
  title: "Components/DataTable",
  component: DataTable,
  tags: ["autodocs"],
  parameters: {
    layout: "padded",
  },
} satisfies Meta<typeof DataTable>;

export default meta;
type Story = StoryObj<typeof meta>;

const simpleCSV = `school,yearGroup,mean,student_n
Focus School Academy,7,3.2,45
Focus School Academy,8,3.5,52
Focus School Academy,9,3.8,48
Focus School Academy,10,3.6,50
Focus School Academy,11,3.4,47`;

const largeCSV = `school,yearGroup,d_sex,d_ethnicity,wave,mean,student_n
Focus School Academy,7,M,White,1,3.2,12
Focus School Academy,7,M,White,2,3.4,12
Focus School Academy,7,M,White,3,3.5,11
Focus School Academy,7,M,Asian,1,3.1,8
Focus School Academy,7,M,Asian,2,3.3,9
Focus School Academy,7,M,Asian,3,3.6,9
Focus School Academy,7,F,White,1,3.4,14
Focus School Academy,7,F,White,2,3.5,13
Focus School Academy,7,F,White,3,3.7,13
Focus School Academy,7,F,Asian,1,3.3,7
Focus School Academy,7,F,Asian,2,3.5,8
Focus School Academy,7,F,Asian,3,3.8,8`;

const emptyCSV = ``;

export const Simple: Story = {
  args: {
    csv: simpleCSV,
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);

    // Test: Verify basic table rendering with proper semantic structure
    const table = canvas.getByRole("table");
    await expect(table).toBeInTheDocument();

    // Test: Verify correct number of columns (4: school, yearGroup, mean, student_n)
    const columnHeaders = canvas.getAllByRole("columnheader");
    await expect(columnHeaders.length).toBe(4);

    // Test: Verify column ordering is correct
    await expect(columnHeaders[0]).toHaveTextContent(/School/i);
    await expect(columnHeaders[1]).toHaveTextContent(/Year group/i);
    await expect(columnHeaders[2]).toHaveTextContent(/Mean/i);
    await expect(columnHeaders[3]).toHaveTextContent(/Student count|N/i);

    // Test: Verify correct number of rows (5 year groups)
    const rows = canvas.getAllByRole("row");
    await expect(rows.length).toBe(6); // 1 header row + 5 data rows

    // Test: Verify sortable column headers are present (clickable)
    const columnHeader = canvas.getByRole("columnheader", { name: /School/i });
    await expect(columnHeader).toBeInTheDocument();

    // Test: Verify specific data is rendered correctly
    const allCells = canvas.getAllByRole("cell");
    await expect(allCells.length).toBe(20); // 5 rows × 4 columns
  },
};

export const LargeTable: Story = {
  args: {
    csv: largeCSV,
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);

    // Test: Verify table handles many columns correctly
    const table = canvas.getByRole("table");
    await expect(table).toBeInTheDocument();

    // Test: Verify correct number of columns (7: school, yearGroup, d_sex, d_ethnicity, wave, mean, student_n)
    const columnHeaders = canvas.getAllByRole("columnheader");
    await expect(columnHeaders.length).toBe(7);

    // Test: Verify column ordering
    await expect(columnHeaders[0]).toHaveTextContent(/School/i);
    await expect(columnHeaders[1]).toHaveTextContent(/Year group/i);
    await expect(columnHeaders[2]).toHaveTextContent(/Sex/i);
    await expect(columnHeaders[3]).toHaveTextContent(/Ethnicity/i);
    await expect(columnHeaders[4]).toHaveTextContent(/Wave/i);
    await expect(columnHeaders[5]).toHaveTextContent(/Mean/i);
    await expect(columnHeaders[6]).toHaveTextContent(/Student count|N/i);

    // Test: Verify correct number of rows (12 data rows from CSV)
    const rows = canvas.getAllByRole("row");
    await expect(rows.length).toBe(13); // 1 header + 12 data rows

    // Verify multiple demographic columns are present
    const sexHeader = canvas.getByRole("columnheader", { name: /Sex/i });
    await expect(sexHeader).toBeInTheDocument();

    const ethnicityHeader = canvas.getByRole("columnheader", {
      name: /Ethnicity/i,
    });
    await expect(ethnicityHeader).toBeInTheDocument();

    const waveHeader = canvas.getByRole("columnheader", { name: /Wave/i });
    await expect(waveHeader).toBeInTheDocument();

    const yearGroupHeader = canvas.getByRole("columnheader", {
      name: /Year group/i,
    });
    await expect(yearGroupHeader).toBeInTheDocument();

    // Verify table has multiple rows of data
    const allCells = canvas.getAllByRole("cell");
    await expect(allCells.length).toBe(84); // 12 rows × 7 columns
  },
};

export const Empty: Story = {
  args: {
    csv: emptyCSV,
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);

    // Test: Verify empty state message displays when no data
    const noDataText = canvas.getByText("No data available.");
    await expect(noDataText).toBeInTheDocument();

    // Verify no table is rendered when empty
    const table = canvas.queryByRole("table");
    await expect(table).not.toBeInTheDocument();
  },
};

export const SingleRow: Story = {
  args: {
    csv: `school,mean,student_n
Focus School Academy,3.5,125`,
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);

    // Test: Verify table with minimal data (single row)
    const table = canvas.getByRole("table");
    await expect(table).toBeInTheDocument();

    // Verify the single row data is accessible
    const cells = canvas.getAllByRole("cell");
    await expect(cells.length).toBeGreaterThan(0);
  },
};

export const ManyColumns: Story = {
  args: {
    csv: `school,yearGroup,d_sex,d_ethnicity,d_age,wave,class,bw_wbeing_1,bw_wbeing_2,bw_wbeing_3,bw_wbeing_total,student_n
Focus School Academy,7,M,White,12,1,7A,3.2,3.4,3.1,3.3,8
Focus School Academy,7,F,Asian,11,1,7B,3.5,3.6,3.4,3.5,7`,
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);

    // Test: Verify sorting functionality by clicking column header
    const table = canvas.getByRole("table");
    await expect(table).toBeInTheDocument();

    // Find and click a sortable column header
    const classHeader = canvas.getByRole("columnheader", { name: /Class/i });
    await expect(classHeader).toBeInTheDocument();

    // Click to sort
    await userEvent.click(classHeader);

    // Verify sort indicator appears (↑ or ↓)
    const sortedHeader = canvas.getByText(/Class/i).closest("th");
    await expect(sortedHeader?.textContent).toMatch(/[↑↓]/);
  },
};
