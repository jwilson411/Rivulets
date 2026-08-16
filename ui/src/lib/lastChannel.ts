// The icon rail's Hash icon "opens the last channel, or the list"
// (04-information-architecture.md). Remembered per browser.

const KEY = 'rivulets-last-channel';

export function readLastChannel(): string | null {
	if (typeof localStorage === 'undefined') return null;
	return localStorage.getItem(KEY);
}

export function writeLastChannel(id: string): void {
	if (typeof localStorage === 'undefined') return;
	localStorage.setItem(KEY, id);
}
