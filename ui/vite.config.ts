import { defineConfig } from 'vitest/config';
import { playwright } from '@vitest/browser-playwright';
import tailwindcss from '@tailwindcss/vite';
import adapter from '@sveltejs/adapter-static';
import { sveltekit } from '@sveltejs/kit/vite';

export default defineConfig({
	plugins: [
		tailwindcss(),
		sveltekit({
			compilerOptions: {
				// Force runes mode for the project, except for libraries. Can be removed in svelte 6.
				runes: ({ filename }) =>
					filename.split(/[/\\]/).includes('node_modules') ? undefined : true
			},
			// SPA fallback: adapter-static only prerenders at build time, but every
			// page here needs live data from the App Server API, so there's
			// nothing meaningful to prerender. index.html is served for any
			// path FastAPI doesn't otherwise recognize, and SvelteKit's client
			// router takes over from there (see src/routes/+layout.ts).
			adapter: adapter({ fallback: 'index.html' })
		})
	],
	// In production the built SPA is served by the App Server itself, so
	// `/api/...` is already same-origin. In dev, the App Server runs
	// separately on :8484 (see server/README.md), so proxy it here — the
	// UI code only ever calls relative `/api/v1/...` paths.
	server: {
		proxy: {
			'/api': 'http://127.0.0.1:8484'
		}
	},
	test: {
		expect: { requireAssertions: true },
		// Real tests exist now (src/lib/api/client.test.ts, LoginForm.svelte.test.ts,
		// Sidebar.svelte.test.ts), but keeping this: a project's test glob
		// matching nothing shouldn't fail CI on its own, e.g. if the
		// client/server split above ever ends up with one side temporarily
		// empty during development.
		passWithNoTests: true,
		coverage: {
			provider: 'v8',
			reporter: ['text', 'html'],
			// Without an explicit `include`, coverage only reports files a test
			// actually imported -- files nobody wrote a test for (yet) would
			// silently drop out of the denominator instead of showing as 0%.
			include: ['src/**/*.{ts,svelte}'],
			exclude: [
				'.svelte-kit/**',
				'build/**',
				'**/*.config.{js,ts}',
				'src/app.d.ts',
				'**/*.d.ts',
				'**/*.{test,spec}.{js,ts}',
				// Barrel placeholder with no executable statements ($lib alias re-export dir).
				'src/lib/index.ts',
				// SvelteKit route config (`export const ssr = false`), not app logic.
				'src/routes/+layout.ts'
			],
			thresholds: {
				statements: 95
			}
		},
		projects: [
			{
				extends: './vite.config.ts',
				test: {
					name: 'client',
					browser: {
						enabled: true,
						provider: playwright(),
						instances: [{ browser: 'chromium', headless: true }]
					},
					include: ['src/**/*.svelte.{test,spec}.{js,ts}'],
					exclude: ['src/lib/server/**']
				}
			},

			{
				extends: './vite.config.ts',
				test: {
					name: 'server',
					environment: 'node',
					include: ['src/**/*.{test,spec}.{js,ts}'],
					exclude: ['src/**/*.svelte.{test,spec}.{js,ts}']
				}
			}
		]
	}
});
