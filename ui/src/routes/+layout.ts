// Rivulets' UI is entirely dynamic (live channels/threads/agents fetched
// from the local App Server) — there's nothing for adapter-static to
// prerender, and no SvelteKit server exists at runtime. Every route is
// rendered client-side; see vite.config.ts's `fallback: 'index.html'`.
export const ssr = false;
