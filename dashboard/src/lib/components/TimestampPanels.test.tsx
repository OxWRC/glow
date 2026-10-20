import { describe, it, expect, afterEach, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { formatDateTime } from "../formatDateTime";
import { ApiKeysPanel } from "./ApiKeysPanel";
import { VintagesPanel } from "./VintagesPanel";

const CREATED = "2026-10-07T12:00:00Z";
const EXPIRES = "2027-01-07T23:30:00Z";

vi.mock("../api", () => ({
  listApiKeys: vi.fn().mockResolvedValue([
    {
      id: 1,
      name: "K",
      prefix: "glw_x",
      created_by: "u",
      created_at: "2026-10-07T12:00:00Z",
      expires_at: "2027-01-07T23:30:00Z",
      revoked_at: null,
      last_used_at: null,
      use_count: 0,
    },
  ]),
  createApiKey: vi.fn(),
  revokeApiKey: vi.fn(),
  listVintages: vi.fn().mockResolvedValue([
    {
      prefix: "v1",
      created_at: "2026-10-07T12:00:00Z",
      dataset_version: "d",
      row_count: 1,
      created_by_username: "u",
    },
  ]),
  cutVintage: vi.fn(),
}));

afterEach(cleanup);

const LABEL = /\d:\d\d\s?(AM|PM)\s\S+$/;

describe("timestamps carry a time and zone label", () => {
  it("VintagesPanel", async () => {
    render(<VintagesPanel token="t" locale="en" />);
    const cell = await screen.findByRole("cell", {
      name: formatDateTime(CREATED, "en"),
    });
    expect(cell.textContent).toMatch(LABEL);
  });

  it("ApiKeysPanel expiry is date, time and zone", async () => {
    render(<ApiKeysPanel token="t" locale="en" now={new Date(CREATED)} />);
    const cell = await screen.findByRole("cell", {
      name: formatDateTime(EXPIRES, "en"),
    });
    expect(cell.textContent).toMatch(LABEL);
  });
});
