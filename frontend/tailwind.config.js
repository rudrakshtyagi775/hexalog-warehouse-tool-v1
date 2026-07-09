/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        brand: {
          50:  '#F4EEFF',
          100: '#E6D5FF',
          200: '#C9AAFF',
          300: '#A07DE8',
          400: '#8F62DF',
          500: '#7B4ED6',
          600: '#744C8A',
          700: '#5C3875',
          800: '#442A59',
          900: '#2D1B3D',
          950: '#1A0F24',
        },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui, sans-serif'],
      },
    },
  },
  plugins: [],
}
