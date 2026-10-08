import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

export default defineConfig({
  plugins: [react(), tailwindcss()],
  base: '/',
  build: { outDir: 'dist', emptyOutDir: true },
  server: {
    proxy: {
      '/meta': 'http://localhost:8000',
      '/sponsorships': 'http://localhost:8000',
      '/console/ledger': 'http://localhost:8000',
      '/disbursements': 'http://localhost:8000',
      '/farmers': 'http://localhost:8000',
      '/farms': 'http://localhost:8000',
      '/users': 'http://localhost:8000',
      '/banks': 'http://localhost:8000',
      '/static': 'http://localhost:8000',
      '/docs': 'http://localhost:8000'
    }
  }
});
