/** 呈现约定的回归钉 (docs/architecture.md §7「前端不重算统计量」)。
 *
 * 这些约定曾被写进口碑: null/缺失 ≠ 0、外推值带 * 不冒充实测、区间两端
 * 皆缺时返回 null 而不是猜测。用例钉住的是"重构时最容易被顺手优化掉"
 * 的那几条 —— 数值逻辑本体在 `lib/format.ts`, 测试不渲染组件。
 */

import { describe, expect, it } from "vitest";

import { fmtCi, fmtDelta, fmtPct, fmtRate, fmtScore } from "./format";

describe("fmtRate — 通过率呈现约定", () => {
  it("null = 证据不足 (分母无 valid trial), 绝不呈现为 0%", () => {
    expect(fmtRate(null)).toBe("证据不足");
    expect(fmtRate(undefined)).toBe("证据不足");
    expect(fmtRate(null)).not.toMatch(/0/);
  });

  it("外推值以 * 标注, 不冒充实测值", () => {
    expect(fmtRate(0.6667, true)).toBe("66.7%*");
    expect(fmtRate(0.6667, false)).toBe("66.7%");
    expect(fmtRate(1, true)).toBe("100.0%*");
  });

  it("实测 0 分就是 0.0% (与证据不足可区分)", () => {
    expect(fmtRate(0)).toBe("0.0%");
  });
});

describe("fmtScore / fmtDelta / fmtPct — 缺失 ≠ 零", () => {
  it("连续分数缺失呈现为占位符, 不是 0.000", () => {
    expect(fmtScore(null)).toBe("—");
    expect(fmtScore(0.5)).toBe("0.500");
  });

  it("差值缺失呈现为证据不足, 不是 +0.000", () => {
    expect(fmtDelta(null)).toBe("证据不足");
    expect(fmtDelta(0.25)).toBe("+0.250");
    expect(fmtDelta(-0.25)).toBe("-0.250");
  });

  it("百分比缺失为占位符", () => {
    expect(fmtPct(null)).toBe("—");
    expect(fmtPct(0.87)).toBe("87%");
  });
});

describe("fmtCi — 置信区间呈现约定", () => {
  it("区间整体缺失返回 null (不猜测区间)", () => {
    expect(fmtCi(null)).toBeNull();
    expect(fmtCi(undefined)).toBeNull();
    expect(fmtCi([null, null])).toBeNull();
  });

  it("一端缺失以占位符呈现, 不伪造完整区间", () => {
    expect(fmtCi([null, 1.0])).toBe("[—, 1.000]");
    expect(fmtCi([0.342, null])).toBe("[0.342, —]");
  });

  it("两端齐全按位数呈现", () => {
    expect(fmtCi([0.342, 1.0])).toBe("[0.342, 1.000]");
    expect(fmtCi([0.3, 0.9], 1)).toBe("[0.3, 0.9]");
  });
});
