import { fireEvent, render, screen } from "@testing-library/react";
import type { Employee } from "../types";
import { StatusPicker } from "./StatusPicker";

const employee: Employee = {
  employee_id: "10000001", employee_name: "Fake Employee A", department: null, emp_status: "Active", lwd: null,
  days: { "2026-09-21": "A" }, original_days: { "2026-09-21": "A" }, calculated_days: { "2026-09-21": "A" },
  schedule: { "2026-09-21": "6DAG" }, worked_seconds: { "2026-09-21": 17999 },
};
const target = { employee, date: "2026-09-21", rect: new DOMRect(100, 100, 44, 30) };

describe("StatusPicker", () => {
  it("shows the SAP details and returns the chosen code", () => {
    const onChoose = vi.fn();
    render(<StatusPicker target={target} statuses={["P", "A", "CO"]} current="A" onChoose={onChoose} onClose={() => {}} />);
    expect(screen.getByText(/6DAG · 4:59:59/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("menuitem", { name: /Comp off/ }));
    expect(onChoose).toHaveBeenCalledWith("CO");
  });

  it("closes on Escape", () => {
    const onClose = vi.fn();
    render(<StatusPicker target={target} statuses={["P"]} current={null} onChoose={() => {}} onClose={onClose} />);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });
});
