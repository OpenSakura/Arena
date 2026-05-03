import { describe, expect, it } from "vitest";

import { parseJsonObjectOrNull, parseNumberOrNull } from "./adminParsers";

describe("parseNumberOrNull", () => {
  it("returns null for blank values", () => {
    expect(parseNumberOrNull("")).toBeNull();
    expect(parseNumberOrNull("   ")).toBeNull();
  });

  it("parses finite numeric input", () => {
    expect(parseNumberOrNull(" 1.5 ")).toBe(1.5);
    expect(parseNumberOrNull("0")).toBe(0);
    expect(parseNumberOrNull("-3")).toBe(-3);
  });

  it("throws an error for non-finite values", () => {
    expect(() => parseNumberOrNull("NaN")).toThrow("Invalid number");
    expect(() => parseNumberOrNull("Infinity")).toThrow("Invalid number");
    expect(() => parseNumberOrNull("not-a-number")).toThrow("Invalid number");
  });
});

describe("parseJsonObjectOrNull", () => {
  it("returns null for blank input", () => {
    expect(parseJsonObjectOrNull("  ")).toBeNull();
  });

  it("parses JSON object payloads", () => {
    expect(parseJsonObjectOrNull('{"a":1,"b":"x"}')).toEqual({ a: 1, b: "x" });
  });

  it("throws for non-object JSON values", () => {
    expect(() => parseJsonObjectOrNull("[]")).toThrow("Expected a JSON object");
    expect(() => parseJsonObjectOrNull('"x"')).toThrow("Expected a JSON object");
    expect(() => parseJsonObjectOrNull("123")).toThrow("Expected a JSON object");
    expect(() => parseJsonObjectOrNull("null")).toThrow("Expected a JSON object");
  });

  it("rethrows JSON parse errors", () => {
    expect(() => parseJsonObjectOrNull("{bad json")).toThrow();
  });
});
