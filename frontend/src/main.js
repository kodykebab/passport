import { createApp } from "vue";
import App from "./App.vue";
import RecoveryNotice from "./RecoveryNotice.vue";
import "frappe-ui/style.css";

const payload = document.getElementById("passport-data");
if (payload)
  createApp(App, { data: JSON.parse(payload.textContent) }).mount(
    "#passport-ui",
  );

export async function mountNotice(host, retry, stylesheet) {
  const response = await fetch(stylesheet);
  if (!response.ok) throw new Error("Unable to load recovery styles");
  const style = document.createElement("style");
  // Keep frappe-ui's reset and theme tokens inside the notice, away from Desk.
  style.textContent = (await response.text()).replaceAll(":root", ":host");
  const root = host.attachShadow({ mode: "open" });
  const target = document.createElement("div");
  root.append(style, target);
  const app = createApp(RecoveryNotice, { retry });
  app.mount(target);
  return () => app.unmount();
}
