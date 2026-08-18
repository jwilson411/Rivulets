import { describe, expect, it } from 'vitest';
import {
	GOOGLE_CAPABILITIES,
	GOOGLE_DEFAULT_CAPABILITIES,
	formatGoogleCapabilities,
	googleCapabilityName,
	toggleGoogleCapability
} from './googleCapabilities';

describe('googleCapabilities', () => {
	it('defaults to every read surface and no writes', () => {
		expect(GOOGLE_DEFAULT_CAPABILITIES).toContain('gmail_read');
		expect(GOOGLE_DEFAULT_CAPABILITIES).not.toContain('gmail_write');
		expect(GOOGLE_DEFAULT_CAPABILITIES).not.toContain('meet_write');
	});

	it('names a box for the Settings checkbox', () => {
		const gmailRead = GOOGLE_CAPABILITIES.find((cap) => cap.id === 'gmail_read');
		expect(gmailRead && googleCapabilityName(gmailRead)).toBe('Gmail — read');
	});

	it('turns write on with its read sibling, and keeps read while write is on', () => {
		const withSend = toggleGoogleCapability(
			[...GOOGLE_DEFAULT_CAPABILITIES],
			GOOGLE_CAPABILITIES,
			'gmail_write',
			true
		);
		expect(withSend).toContain('gmail_write');
		expect(withSend).toContain('gmail_read');
		expect(toggleGoogleCapability(withSend, GOOGLE_CAPABILITIES, 'gmail_read', false)).toContain(
			'gmail_read'
		);
	});

	it('summarizes granted access for the account card', () => {
		expect(formatGoogleCapabilities(['gmail_read', 'gmail_write'], GOOGLE_CAPABILITIES)).toBe(
			'Gmail read, send and draft'
		);
	});
});
