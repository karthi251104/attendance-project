// Attendance grid: track edits, update totals live, save only changed cells in one POST.
(() => {
  const grid = document.getElementById("grid");
  if (!grid || grid.dataset.editable !== "true") return;

  const saveButton = document.getElementById("save");
  const pending = document.getElementById("pending");
  const message = document.getElementById("message");
  const comment = document.getElementById("comment");
  const counted = ["P", "A", "OFF", "PH", "CO", "ML", "PL"];
  const dirty = new Map(); // "employeeId|date" -> select element

  const keyOf = (select) => `${select.closest("tr").dataset.employeeId}|${select.dataset.date}`;

  function updateTotals(row) {
    const counts = Object.fromEntries(counted.map((code) => [code, 0]));
    row.querySelectorAll("td.day select").forEach((s) => { if (s.value in counts) counts[s.value] += 1; });
    counts.Total = counted.reduce((sum, code) => sum + counts[code], 0);
    row.querySelectorAll("td.total").forEach((cell) => { cell.textContent = counts[cell.dataset.total]; });
  }

  function refreshBar() {
    const n = dirty.size;
    pending.textContent = n === 0 ? "No unsaved changes" : `${n} unsaved change${n === 1 ? "" : "s"}`;
    saveButton.disabled = n === 0;
  }

  function showMessage(text, kind) {
    message.textContent = text;
    message.className = `alert alert-${kind}`;
  }

  grid.addEventListener("change", (event) => {
    const select = event.target;
    if (select.tagName !== "SELECT") return;
    const changed = select.value !== select.dataset.loaded;
    select.closest("td").classList.toggle("dirty", changed);
    if (changed) dirty.set(keyOf(select), select); else dirty.delete(keyOf(select));
    updateTotals(select.closest("tr"));
    refreshBar();
  });

  saveButton.addEventListener("click", async () => {
    const changes = [...dirty.values()].map((select) => ({
      employee_id: select.closest("tr").dataset.employeeId,
      date: select.dataset.date,
      value: select.value,
      old_value: select.dataset.loaded || null,
    }));
    saveButton.disabled = true;
    saveButton.textContent = "Saving…";
    try {
      const response = await fetch(`/api/cycles/${grid.dataset.cycleId}/changes`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": grid.dataset.csrf },
        body: JSON.stringify({ changes, comment: comment.value.trim() || null }),
        credentials: "same-origin",
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) {
        showMessage(body.error || "Saving failed. Please try again.", "error");
        return;
      }
      const conflicted = new Set((body.conflicts || []).map((c) => `${c.employee_id}|${c.date}`));
      for (const [key, select] of dirty) {
        if (conflicted.has(key)) continue;
        select.dataset.loaded = select.value;
        const cell = select.closest("td");
        cell.classList.remove("dirty");
        cell.classList.add("edited");
        dirty.delete(key);
      }
      comment.value = "";
      if (conflicted.size) {
        showMessage(`${body.saved} change(s) saved. ${conflicted.size} day(s) were changed by someone else meanwhile and were not saved. Please reload the page.`, "warn");
      } else {
        showMessage(`${body.saved} change${body.saved === 1 ? "" : "s"} saved.`, "success");
      }
    } catch (error) {
      showMessage("Could not reach the server. Your changes are not saved yet. Please try again.", "error");
    } finally {
      saveButton.textContent = "Save changes";
      refreshBar();
    }
  });

  window.addEventListener("beforeunload", (event) => {
    if (dirty.size > 0) {
      event.preventDefault();
      event.returnValue = "";
    }
  });
})();
