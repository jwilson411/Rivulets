// Manual light/dark/system override (issue #13). Dark mode itself already
// works purely via `@media (prefers-color-scheme: dark)` — see
// routes/layout.css's `@custom-variant dark` block. This module only ever
// toggles a `data-theme` attribute on <html>; when the preference is
// "system" the attribute is removed entirely so the CSS's media-query
// fallback (and its live OS-level updates) takes back over untouched.

const STORAGE_KEY = 'rivulets-theme';

export type ThemePreference = 'light' | 'dark' | 'system';

export function readStored(): ThemePreference {
	if (typeof localStorage === 'undefined') return 'system';
	const stored = localStorage.getItem(STORAGE_KEY);
	return stored === 'light' || stored === 'dark' ? stored : 'system';
}

function apply(preference: ThemePreference) {
	if (typeof document === 'undefined') return;
	if (preference === 'system') {
		delete document.documentElement.dataset.theme;
	} else {
		document.documentElement.dataset.theme = preference;
	}
}

let preference = $state<ThemePreference>(readStored());
apply(preference);

export const theme = {
	get preference() {
		return preference;
	},
	set(next: ThemePreference) {
		preference = next;
		localStorage.setItem(STORAGE_KEY, next);
		apply(next);
	}
};
