import { describe, it, expect } from "vitest";
import { getExample, getAllExamples } from "./contractExamples";

describe("Contract Examples", () => {
  it("should load all contract examples", () => {
    const examples = getAllExamples();
    console.log("Loaded examples:", examples.length);
    console.log(
      "Example IDs:",
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      examples.map((e: any) => e.id),
    );
    expect(examples.length).toBeGreaterThan(0);
  });

  it("should load specific example", () => {
    const example = getExample("admin.me.user");
    console.log("Loaded example:", example);
    expect(example).toBeDefined();
    expect(example?.id).toBe("admin.me.user");
  });
});
