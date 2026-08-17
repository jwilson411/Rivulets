import { generateMnemonic } from 'bip39';
import { describe, expect, it } from 'vitest';
import { isUnlockPhraseReady } from './mnemonic';

const VALID =
	'abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about';

describe('isUnlockPhraseReady', () => {
	it('rejects empty and partial phrases', () => {
		expect(isUnlockPhraseReady('')).toBe(false);
		expect(isUnlockPhraseReady('   ')).toBe(false);
		expect(isUnlockPhraseReady('asdf qwer zxcv random junk words here not valid')).toBe(false);
		expect(isUnlockPhraseReady(VALID.split(' ').slice(0, 11).join(' '))).toBe(false);
	});

	it('rejects 12 tokens that are not a BIP-39 phrase', () => {
		expect(
			isUnlockPhraseReady('one two three four five six seven eight nine ten eleven twelve')
		).toBe(false);
	});

	it('accepts a 12-word BIP-39 phrase, including extra whitespace', () => {
		expect(isUnlockPhraseReady(VALID)).toBe(true);
		expect(isUnlockPhraseReady(`  ${VALID.replaceAll(' ', '   ')}  `)).toBe(true);
	});

	it('rejects a valid BIP-39 phrase that is not 12 words', () => {
		expect(isUnlockPhraseReady(generateMnemonic(256))).toBe(false);
	});
});
