import { afterEach, describe, expect, it, vi } from 'vitest';
import {
	compareLastActivity,
	formatBytes,
	formatClock,
	paletteShortcutLabel,
	timeAgo
} from './format';

describe('paletteShortcutLabel', () => {
	afterEach(() => {
		vi.unstubAllGlobals();
	});

	it('prints ⌘K on Apple platforms', () => {
		vi.stubGlobal('navigator', { platform: 'MacIntel' });
		expect(paletteShortcutLabel()).toBe('⌘K');
	});

	it('prints Ctrl+K elsewhere', () => {
		vi.stubGlobal('navigator', { platform: 'Linux x86_64' });
		expect(paletteShortcutLabel()).toBe('Ctrl+K');
	});
});

describe('formatBytes', () => {
	it('formats sub-1024-byte sizes as whole bytes', () => {
		expect(formatBytes(0)).toBe('0 B');
		expect(formatBytes(512)).toBe('512 B');
		expect(formatBytes(1023)).toBe('1023 B');
	});

	it('formats sub-1MB sizes as KB with one decimal place', () => {
		expect(formatBytes(1024)).toBe('1.0 KB');
		expect(formatBytes(2048)).toBe('2.0 KB');
		expect(formatBytes(1536)).toBe('1.5 KB');
	});

	it('formats 1MB and above as MB with one decimal place', () => {
		expect(formatBytes(1024 * 1024)).toBe('1.0 MB');
		expect(formatBytes(5.5 * 1024 * 1024)).toBe('5.5 MB');
	});
});

describe('formatClock', () => {
	it('formats an ISO timestamp as a localized hour:minute string', () => {
		const result = formatClock('2024-06-01T15:04:00Z');
		expect(result).toMatch(/\d{1,2}:\d{2}/);
	});
});

describe('compareLastActivity', () => {
	it('orders by lastAt so a reply beats a newer-but-idle thread', () => {
		const olderReplied = { lastAt: '2026-01-02T12:00:00Z', createdAt: '2026-01-01T00:00:00Z' };
		const newerIdle = { lastAt: '2026-01-01T12:00:00Z', createdAt: '2026-01-01T12:00:00Z' };
		expect([newerIdle, olderReplied].sort(compareLastActivity)).toEqual([olderReplied, newerIdle]);
	});

	it('falls back to createdAt when lastAt is missing', () => {
		const older = { lastAt: null, createdAt: '2026-01-01T00:00:00Z' };
		const newer = { createdAt: '2026-01-02T00:00:00Z' };
		expect([older, newer].sort(compareLastActivity)).toEqual([newer, older]);
	});
});

describe('timeAgo', () => {
	afterEach(() => {
		vi.useRealTimers();
	});

	it('returns "just now" for timestamps under a minute old', () => {
		vi.useFakeTimers();
		vi.setSystemTime(new Date('2024-01-01T00:00:30Z'));
		expect(timeAgo('2024-01-01T00:00:00Z')).toBe('just now');
	});

	it('returns "just now" for a timestamp exactly at now', () => {
		vi.useFakeTimers();
		vi.setSystemTime(new Date('2024-01-01T00:00:00Z'));
		expect(timeAgo('2024-01-01T00:00:00Z')).toBe('just now');
	});

	it('returns minutes ago for timestamps under an hour old', () => {
		vi.useFakeTimers();
		vi.setSystemTime(new Date('2024-01-01T00:10:00Z'));
		expect(timeAgo('2024-01-01T00:00:00Z')).toBe('10m ago');
	});

	it('returns hours ago for timestamps under a day old', () => {
		vi.useFakeTimers();
		vi.setSystemTime(new Date('2024-01-01T05:00:00Z'));
		expect(timeAgo('2024-01-01T00:00:00Z')).toBe('5h ago');
	});

	it('returns days ago for timestamps under a week old', () => {
		vi.useFakeTimers();
		vi.setSystemTime(new Date('2024-01-04T00:00:00Z'));
		expect(timeAgo('2024-01-01T00:00:00Z')).toBe('3d ago');
	});

	it('falls back to a localized date string for timestamps a week or older', () => {
		vi.useFakeTimers();
		vi.setSystemTime(new Date('2024-01-10T00:00:00Z'));
		expect(timeAgo('2024-01-01T00:00:00Z')).toBe(
			new Date('2024-01-01T00:00:00Z').toLocaleDateString()
		);
	});
});
