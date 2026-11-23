/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  corePlugins: {
    preflight: false,
  },
  theme: {
    extend: {
      colors: {
        indigo: { 50: "#f4f7f3", 100: "#e8f0e9", 200: "#d1ded4", 300: "#b5cbbb", 400: "#91ad99", 500: "#6e927a", 600: "#4e745b", 700: "#3b6048", 800: "#2e4c3b", 900: "#26332c" },
        slate: { 50: "#f8f9f7", 100: "#f2f5f1", 200: "#e1e7e2", 300: "#cbd6ce", 400: "#87968a", 500: "#627067", 600: "#526157", 700: "#405247", 800: "#324239", 900: "#26332c" },
      },
      fontFamily: {
        sans: [
          "-apple-system",
          "BlinkMacSystemFont",
          "'Segoe UI'",
          "Roboto",
          "'Helvetica Neue'",
          "Arial",
          "'PingFang SC'",
          "'Microsoft YaHei'",
          "sans-serif",
        ],
        mono: [
          "'JetBrains Mono'",
          "'SFMono-Regular'",
          "Menlo",
          "Monaco",
          "Consolas",
          "monospace",
        ],
      },
    },
  },
  plugins: [],
};
