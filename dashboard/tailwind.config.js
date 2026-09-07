/** @type {import('tailwindcss').Config} */
export default {
  content: ["./src/**/*.{html,js,svelte,ts}"],
  theme: {
    extend: {
      colors: {
        brand: {
          50: "#eef4fa",
          100: "#d3e2f0",
          500: "#2f6aa3",
          600: "#265683",
          700: "#224e80",
          900: "#152f4d",
        },
        "brand-teal": "#2f8280",
        "brand-orange": "#ff9900",
      },
    },
  },
  plugins: [],
};
