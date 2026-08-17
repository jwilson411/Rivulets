export function paletteShortcutLabel(): string {
	// The palette binds both meta and ctrl; this is only the hint we
	// print in chrome so people can discover ⌘K / Ctrl+K.
	if (typeof navigator !== 'undefined' && /Mac|iPhone|iPad|iPod/i.test(navigator.platform)) {
		return '⌘K';
	}
	return 'Ctrl+K';
}

export function formatBytes(bytes: number): string {
	if (bytes < 1024) return `${bytes} B`;
	if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
	return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function formatClock(iso: string): string {
	return new Date(iso).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
}

export function timeAgo(iso: string): string {
	const diffMs = Date.now() - new Date(iso).getTime();
	const seconds = Math.max(0, Math.round(diffMs / 1000));
	if (seconds < 60) return 'just now';
	const minutes = Math.round(seconds / 60);
	if (minutes < 60) return `${minutes}m ago`;
	const hours = Math.round(minutes / 60);
	if (hours < 24) return `${hours}h ago`;
	const days = Math.round(hours / 24);
	if (days < 7) return `${days}d ago`;
	return new Date(iso).toLocaleDateString();
}
