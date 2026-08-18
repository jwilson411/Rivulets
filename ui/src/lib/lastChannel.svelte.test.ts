import { afterEach, describe, expect, it } from 'vitest';
import { readLastChannel, writeLastChannel } from './lastChannel';

describe('lastChannel', () => {
	afterEach(() => {
		localStorage.removeItem('rivulets-last-channel');
	});

	it('returns null when nothing has been stored', () => {
		expect(readLastChannel()).toBeNull();
	});

	it('round-trips the last channel id', () => {
		writeLastChannel('chan-9');
		expect(readLastChannel()).toBe('chan-9');
	});
});
