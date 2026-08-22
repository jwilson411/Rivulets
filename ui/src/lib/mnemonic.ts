// #421: Unlock only enables on 12 English BIP-39 words. Checksum is
// still the server's (keys.is_valid_mnemonic).
import english from 'bip39/src/wordlists/english.json';

const UNLOCK_WORD_COUNT = 12;
const ENGLISH_WORDS = new Set(english);

// #518: the generated-phrase confirm gate quizzes a sample of the phrase
// rather than demanding all twelve words back. Random positions (not a
// fixed sample) so "just memorize words 1-3" never becomes a habit, and
// sorted so the quiz reads in phrase order. Lives here (not inline in
// LoginForm) so component tests can pin the sampled positions.
export function sampleWordIndices(count: number, total: number): number[] {
	const indices = Array.from({ length: total }, (_, i) => i);
	for (let i = indices.length - 1; i > 0; i--) {
		const j = Math.floor(Math.random() * (i + 1));
		[indices[i], indices[j]] = [indices[j], indices[i]];
	}
	return indices.slice(0, count).sort((a, b) => a - b);
}

export function isUnlockPhraseReady(phrase: string): boolean {
	const words = phrase
		.trim()
		.split(/\s+/)
		.filter((word) => word.length > 0);
	return (
		words.length === UNLOCK_WORD_COUNT &&
		words.every((word) => ENGLISH_WORDS.has(word.toLowerCase()))
	);
}
