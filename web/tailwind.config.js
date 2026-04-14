/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        brand: {
          DEFAULT: '#4361ee',
          soft: '#eef2ff',
          ring: '#c7d2fe',
        },
      },
      fontFamily: {
        sans: ['DM Sans', 'system-ui', 'PingFang SC', 'Microsoft YaHei', 'Noto Sans CJK SC', 'sans-serif'],
        mono: ['Fira Code', 'PingFang SC', 'Microsoft YaHei', 'monospace'],
      },
    },
  },
  plugins: [],
};
