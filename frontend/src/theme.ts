export type AppTheme = "light" | "dark";
export const getTheme = (): AppTheme => document.documentElement.dataset.theme === "light" ? "light" : "dark";

const button = document.createElement("button");
button.type = "button";
button.className = "theme-toggle";

function updateButton(): void {
  const dark = getTheme() === "dark";
  button.textContent = dark ? "☀ 浅色" : "☾ 深色";
  button.setAttribute("aria-label", dark ? "切换到浅色主题" : "切换到深色主题");
  button.setAttribute("aria-pressed", String(dark));
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", dark ? "#0b1424" : "#f5f9ff");
}

function applyTheme(value: AppTheme): void {
  document.documentElement.dataset.theme = value;
  updateButton();
  window.dispatchEvent(new CustomEvent("huimai-theme-change", { detail: value }));
}

button.addEventListener("click", () => {
  const value = getTheme() === "dark" ? "light" : "dark";
  try { localStorage.setItem("huimai-theme", value); } catch { /* 当前页面仍正常切换。 */ }
  applyTheme(value);
});
window.addEventListener("storage", (event) => {
  if (event.key === "huimai-theme" && (event.newValue === "light" || event.newValue === "dark")) {
    applyTheme(event.newValue);
  }
});
const header = document.querySelector("#app-header, .snapshot-header");
if (header) header.append(button);
else {
  button.classList.add("theme-toggle-floating");
  document.body.append(button);
}
updateButton();
