import { describe, expect, it } from "vitest";
import { cls, compact, dirClass, money, num, pct, signedPct } from "./format";

describe("pct", () => {
  it("converts a fraction to a percentage with two decimals", () => {
    expect(pct(0.1234)).toBe("12.34%");
  });

  it("honours a custom number of decimals", () => {
    expect(pct(0.1234, 0)).toBe("12%");
    expect(pct(0.12345, 3)).toBe("12.345%");
  });

  it("keeps the minus sign for negative values", () => {
    expect(pct(-0.05)).toBe("-5.00%");
  });
});

describe("signedPct", () => {
  it("adds a plus sign to gains", () => {
    expect(signedPct(0.015)).toBe("+1.50%");
  });

  it("treats zero as a gain", () => {
    expect(signedPct(0)).toBe("+0.00%");
  });

  it("leaves losses with their own minus sign", () => {
    expect(signedPct(-0.015)).toBe("-1.50%");
  });
});

describe("num", () => {
  it("groups thousands and pads to two decimals", () => {
    expect(num(1234567.8)).toBe("1,234,567.80");
  });

  it("honours a custom number of decimals", () => {
    expect(num(3.14159, 3)).toBe("3.142");
    expect(num(42, 0)).toBe("42");
  });
});

describe("money", () => {
  it("formats whole US dollars", () => {
    expect(money(1234567)).toBe("$1,234,567");
  });

  it("rounds away the cents", () => {
    expect(money(99.6)).toBe("$100");
  });

  it("puts the minus sign before the currency symbol", () => {
    expect(money(-2500)).toBe("-$2,500");
  });
});

describe("compact", () => {
  it.each([
    [999, "999"],
    [1500, "1.5K"],
    [2_500_000, "2.5M"],
    [3_000_000_000, "3B"],
  ])("abbreviates %d as %s", (value, expected) => {
    expect(compact(value)).toBe(expected);
  });
});

describe("cls", () => {
  it("joins the class names that are present", () => {
    expect(cls("a", "b")).toBe("a b");
  });

  it("drops false and undefined entries", () => {
    expect(cls("a", false, undefined, "b")).toBe("a b");
  });

  it("drops empty strings", () => {
    expect(cls("value", "")).toBe("value");
  });

  it("returns an empty string when nothing is left", () => {
    expect(cls(false, undefined)).toBe("");
  });
});

describe("dirClass", () => {
  it.each([
    [0.01, "up"],
    [-0.01, "down"],
    [0, ""],
  ])("maps %d to %j", (value, expected) => {
    expect(dirClass(value)).toBe(expected);
  });
});
