// Real browser DOM (vite.config.ts's "client" vitest project) — needed for
// real localStorage and a real document.documentElement to assert against.
//
// `theme` is a module singleton whose $state is seeded once at import time
// from readStored(), so it can't be re-seeded mid-suite by re-importing —
// vi.resetModules() doesn't force a fresh evaluation of an already-loaded
// module under Vitest's browser-mode runner, it just returns the cached
// module. readStored() itself is exported and tested directly below instead;
// `theme` is only exercised through its public set() API, which is real
// runtime behavior regardless of import caching.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { readStored, theme } from './theme.svelte';

const STORAGE_KEY = 'rivulets-theme';

beforeEach(() => {
	localStorage.removeItem(STORAGE_KEY);
	delete document.documentElement.dataset.theme;
});

afterEach(() => {
	vi.unstubAllGlobals();
	localStorage.removeItem(STORAGE_KEY);
	delete document.documentElement.dataset.theme;
});

describe('readStored', () => {
	it('defaults to system when nothing is stored', () => {
		expect(readStored()).toBe('system');
	});

	it('returns a stored light/dark preference', () => {
		localStorage.setItem(STORAGE_KEY, 'dark');
		expect(readStored()).toBe('dark');

		localStorage.setItem(STORAGE_KEY, 'light');
		expect(readStored()).toBe('light');
	});

	it('ignores a corrupt stored value and falls back to system', () => {
		localStorage.setItem(STORAGE_KEY, 'sepia');
		expect(readStored()).toBe('system');
	});

	it('defaults to system when localStorage is unavailable (SSR)', () => {
		vi.stubGlobal('localStorage', undefined);
		expect(readStored()).toBe('system');
	});
});

describe('theme.set()', () => {
	it('persists the choice and updates the html attribute', () => {
		theme.set('dark');
		expect(theme.preference).toBe('dark');
		expect(document.documentElement.dataset.theme).toBe('dark');
		expect(localStorage.getItem(STORAGE_KEY)).toBe('dark');

		theme.set('light');
		expect(document.documentElement.dataset.theme).toBe('light');
		expect(localStorage.getItem(STORAGE_KEY)).toBe('light');

		theme.set('system');
		expect(document.documentElement.dataset.theme).toBeUndefined();
		expect(localStorage.getItem(STORAGE_KEY)).toBe('system');
	});

	// The `typeof document === 'undefined'` SSR guard inside apply() isn't
	// exercised here: vitest-browser-svelte runs against a real Chromium
	// `document`, whose global binding isn't configurable, so it can't be
	// stubbed away the way `localStorage` above can (see readStored's SSR
	// test). That guard is otherwise identical in shape and intent to the
	// localStorage one, which the above test does cover.
});
