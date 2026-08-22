import { generateMnemonic } from 'bip39';
import { describe, expect, it } from 'vitest';
import { isUnlockPhraseReady, sampleWordIndices } from './mnemonic';

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

describe('sampleWordIndices (#518)', () => {
	it('returns the requested number of distinct, in-range, sorted indices', () => {
		for (let run = 0; run < 50; run++) {
			const indices = sampleWordIndices(3, 12);
			expect(indices).toHaveLength(3);
			expect(new Set(indices).size).toBe(3);
			expect([...indices].sort((a, b) => a - b)).toEqual(indices);
			for (const index of indices) {
				expect(index).toBeGreaterThanOrEqual(0);
				expect(index).toBeLessThan(12);
			}
		}
	});

	it('covers every position over enough draws, not a fixed sample', () => {
		const seen = new Set<number>();
		for (let run = 0; run < 200; run++) {
			for (const index of sampleWordIndices(3, 12)) seen.add(index);
		}
		expect(seen.size).toBe(12);
	});
});
