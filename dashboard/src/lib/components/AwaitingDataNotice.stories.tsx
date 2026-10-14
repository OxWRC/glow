import type { Meta, StoryObj } from "@storybook/react-vite";
import { expect, within } from "storybook/test";
import { getExample } from "../mocks/contractExamples";
import type { PeriodCounts } from "../api";
import { AwaitingDataNotice } from "./AwaitingDataNotice";

const counts = getExample("query.counts")!.response.counts as PeriodCounts;

const meta = {
  title: "Components/AwaitingDataNotice",
  component: AwaitingDataNotice,
  args: { locale: "en", isAdmin: false, counts, schoolName: null },
} satisfies Meta<typeof AwaitingDataNotice>;

export default meta;
type Story = StoryObj<typeof meta>;

export const SchoolUser: Story = {
  args: { schoolName: "Focus School Academy" },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(
      canvas.getByRole("heading", { name: /waiting for data/i }),
    ).toBeVisible();
    await expect(
      canvas.getByText(/results will appear here once/i),
    ).toBeVisible();
    // Not theirs to act on: no pointer to the admin page.
    await expect(canvas.queryByRole("link")).not.toBeInTheDocument();
    const table = canvas.getByRole("table", {
      name: /collected so far at Focus School Academy/i,
    });
    const rows = within(table).getAllByRole("row");
    await expect(within(rows[1]).getByText("2025-2026")).toBeVisible();
    await expect(within(rows[1]).getByText("412")).toBeVisible();
    await expect(within(rows[2]).getByText(/too few to show/i)).toBeVisible();
  },
};

export const Admin: Story = {
  args: { isAdmin: true },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText(/cut a vintage/i)).toBeVisible();
    await expect(
      canvas.getByRole("link", { name: /go to vintages/i }),
    ).toHaveAttribute("href", "/en/admin#vintages");
    await expect(
      canvas.getByRole("table", {
        name: /collected so far across all schools/i,
      }),
    ).toBeVisible();
  },
};

export const NothingCollectedYet: Story = {
  args: { counts: {} },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(
      canvas.getByText(/no responses have been collected yet/i),
    ).toBeVisible();
    await expect(canvas.queryByRole("table")).not.toBeInTheDocument();
  },
};

export const CountsUnavailable: Story = {
  args: { counts: null },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    // The notice still stands on its own when the counts can't be fetched.
    await expect(
      canvas.getByRole("heading", { name: /waiting for data/i }),
    ).toBeVisible();
    await expect(
      canvas.getByText(/counts are unavailable right now/i),
    ).toBeVisible();
    await expect(canvas.queryByRole("table")).not.toBeInTheDocument();
  },
};
