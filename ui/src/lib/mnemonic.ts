// #421: Unlock only enables on 12 English BIP-39 words. Checksum is
// still the server's (keys.is_valid_mnemonic).
import english from 'bip39/src/wordlists/english.json';

const UNLOCK_WORD_COUNT = 12;
const ENGLISH_WORDS = new Set(english);

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
