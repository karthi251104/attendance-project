import { applyEdit, editKey, statusTone, totals, withEdits, type PendingEdit } from "./attendance";

describe("totals", () => {
  it("counts each code like the Excel COUNTIF columns", () => {
    const counts = totals({ d1: "P", d2: "P", d3: "A", d4: "OFF", d5: "?", d6: null, d7: "CO" });
    expect(counts).toMatchObject({ P: 2, A: 1, OFF: 1, CO: 1, ML: 0, PL: 0, PH: 0, Total: 5 });
  });
});

describe("pending edits", () => {
  it("records a change and forgets it when set back to the loaded value", () => {
    let pending = new Map<string, PendingEdit>();
    pending = applyEdit(pending, "1", "2026-09-21", "CO", "A");
    expect(pending.get(editKey("1", "2026-09-21"))).toEqual({ employee_id: "1", date: "2026-09-21", value: "CO", old_value: "A" });
    pending = applyEdit(pending, "1", "2026-09-21", "A", "A");
    expect(pending.size).toBe(0);
  });

  it("never mutates the previous map (React state)", () => {
    const before = new Map<string, PendingEdit>();
    const after = applyEdit(before, "1", "d", "P", null);
    expect(before.size).toBe(0);
    expect(after.size).toBe(1);
  });

  it("applies only this employee's edits", () => {
    let pending = applyEdit(new Map(), "1", "d1", "CO", "A");
    pending = applyEdit(pending, "2", "d1", "ML", "P");
    expect(withEdits("1", { d1: "A", d2: "P" }, pending)).toEqual({ d1: "CO", d2: "P" });
  });
});

describe("statusTone", () => {
  it("maps codes to colours and unknown or empty values to neutral", () => {
    expect(statusTone("P")).toBe("green");
    expect(statusTone("A")).toBe("red");
    expect(statusTone(null)).toBe("empty");
    expect(statusTone("ZZ")).toBe("empty");
  });
});
