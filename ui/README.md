# Rivulets UI

SvelteKit SPA for [Rivulets](../README.md) — the App Server (`../server`) serves this build as a static bundle, and the UI talks to it exclusively via `/api/v1/*` (see `vite.config.ts`'s dev proxy).

## Developing

Run the App Server first (`../server`), then:

```sh
npm install
npm run dev
```

## Checks

```sh
npm run lint      # prettier + eslint
npm run check     # svelte-check (types)
npm run test:unit # vitest — client project (real Chromium) + server project (node)
npm run build     # production build, output in build/
```
