import preset, { content } from "frappe-ui/tailwind";
export default {
  presets: [preset],
  content: [...content, "./src/**/*.{vue,js}"],
};
