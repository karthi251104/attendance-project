import { dayHeader, formatDate, formatDateTime, formatDuration, plural } from "./format";

describe("format", () => {
  it("shows dates as dd/mm/yyyy without a time zone shift", () => {
    expect(formatDate("2026-09-21")).toBe("21/09/2026");
    expect(formatDate(null)).toBe("");
  });

  it("shows timestamps in the portal's time zone", () => {
    // 18:29:59 UTC is 23:59:59 in India.
    expect(formatDateTime("2026-11-05T18:29:59+00:00", "Asia/Kolkata")).toBe("05/11/2026 23:59");
  });

  it("labels day columns and weekends", () => {
    expect(dayHeader("2026-09-20")).toEqual({ label: "20/09", weekday: "Sun", weekend: true });
    expect(dayHeader("2026-09-21").weekend).toBe(false);
  });

  it("formats worked hours like SAP", () => {
    expect(formatDuration(17999)).toBe("4:59:59");
    expect(formatDuration(undefined)).toBe("");
  });

  it("pluralises", () => {
    expect(plural(1, "change")).toBe("1 change");
    expect(plural(3, "change")).toBe("3 changes");
  });
});
