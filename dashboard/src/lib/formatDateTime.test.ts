import { describe, it, expect } from "vitest";
import { formatDateTime } from "./formatDateTime";

const ISO = "2026-10-07T12:00:00Z";

describe("formatDateTime", () => {
  it("renders the instant in Auckland on the next day, with a zone label", () => {
    const s = formatDateTime(ISO, "en", "Pacific/Auckland");
    expect(s).toContain("Oct 8");
    expect(s).toMatch(/(NZDT|GMT\+13)/);
  });

  it("renders the instant in Los Angeles with a zone label", () => {
    const s = formatDateTime(ISO, "en", "America/Los_Angeles");
    expect(s).toContain("Oct 7");
    expect(s).toContain("5:00");
    expect(s).toContain("PDT");
  });

  it("is the same instant in both zones", () => {
    const a = new Date(ISO).getTime();
    expect(a).toBe(Date.parse("2026-10-08T01:00:00+13:00"));
    expect(a).toBe(Date.parse("2026-10-07T05:00:00-07:00"));
  });

  it("defaults to the viewer's zone", () => {
    expect(formatDateTime(ISO, "en")).toBe(
      new Intl.DateTimeFormat("en", {
        year: "numeric",
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
        timeZoneName: "short",
      }).format(new Date(ISO)),
    );
  });

  it("returns unparseable input unchanged", () => {
    expect(formatDateTime("not a date", "en")).toBe("not a date");
  });
});
