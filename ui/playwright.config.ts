import { defineConfig } from '@playwright/test';

// CI's e2e-smoke job (ci.yml) already has the real packaged binary running
// (server + bundled UI, same origin) and points this at it via
// PLAYWRIGHT_BASE_URL -- these tests exercise the API through the UI, so
// they need the actual App Server, not just the static SvelteKit build.
// Without that env var (local `npm run test:e2e`), fall back to building
// and previewing the UI and starting our own server for it.
const baseURL = process.env.PLAYWRIGHT_BASE_URL ?? 'http://127.0.0.1:4173';

export default defineConfig({
	use: { baseURL },
	webServer: process.env.PLAYWRIGHT_BASE_URL
		? undefined
		: { command: 'npm run build && npm run preview', port: 4173 },
	testMatch: '**/*.e2e.{ts,js}'
});
