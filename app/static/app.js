// Small page behaviours: confirm dialogs, "busy" buttons, upload size check.
document.addEventListener("submit", (event) => {
  const form = event.target;
  if (form.dataset.confirm && !window.confirm(form.dataset.confirm)) {
    event.preventDefault();
    return;
  }
  const fileInput = form.querySelector("input[type=file][data-max-mb]");
  if (fileInput && fileInput.files[0] && fileInput.files[0].size > Number(fileInput.dataset.maxMb) * 1024 * 1024) {
    event.preventDefault();
    window.alert(`The file is larger than ${fileInput.dataset.maxMb} MB.`);
    return;
  }
  const button = form.querySelector("button[type=submit]");
  if (!button) return;
  if (form.hasAttribute("data-download")) {
    // The file arrives as a download, so the page stays; re-enable the button after a while.
    const label = button.textContent;
    button.textContent = "Preparing download…";
    button.disabled = true;
    setTimeout(() => { button.textContent = label; button.disabled = false; }, 15000);
  } else if (form.dataset.busyText) {
    button.textContent = form.dataset.busyText;
    setTimeout(() => { button.disabled = true; }, 0); // after the submit has started
  }
});
