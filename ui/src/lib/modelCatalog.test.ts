import { describe, expect, it } from 'vitest';
import { AUTO_MODEL, AUTO_MODEL_VALUE, CUSTOM_MODEL_VALUE, MODEL_CATALOG } from './modelCatalog';
import type { ProviderKind } from './api/providers';

const PROVIDER_KINDS: ProviderKind[] = [
	'anthropic',
	'openai',
	'deepseek',
	'google',
	'mistral',
	'groq',
	'xai',
	'qwen',
	'cohere',
	'ollama',
	'openai_compatible'
];

describe('MODEL_CATALOG', () => {
	it('has an entry for every ProviderKind', () => {
		for (const kind of PROVIDER_KINDS) {
			expect(MODEL_CATALOG[kind]).toBeDefined();
			expect(Array.isArray(MODEL_CATALOG[kind])).toBe(true);
		}
	});

	it('gives every model option a non-empty id and label', () => {
		for (const kind of PROVIDER_KINDS) {
			for (const option of MODEL_CATALOG[kind]) {
				expect(option.id.length).toBeGreaterThan(0);
				expect(option.label.length).toBeGreaterThan(0);
			}
		}
	});

	it('has no duplicate ids within a single provider group', () => {
		for (const kind of PROVIDER_KINDS) {
			const ids = MODEL_CATALOG[kind].map((option) => option.id);
			expect(new Set(ids).size).toBe(ids.length);
		}
	});

	it('leaves openai_compatible and ollama catalog-less since their models are self-hosted and unpredictable', () => {
		expect(MODEL_CATALOG.openai_compatible).toEqual([]);
		expect(MODEL_CATALOG.ollama).toEqual([]);
	});

	it('includes anthropic and openai catalogs with at least one option', () => {
		expect(MODEL_CATALOG.anthropic.length).toBeGreaterThan(0);
		expect(MODEL_CATALOG.openai.length).toBeGreaterThan(0);
		expect(MODEL_CATALOG.deepseek.length).toBeGreaterThan(0);
	});
});

describe('sentinel values', () => {
	it('defines distinct sentinels for the custom-model and auto-model options', () => {
		expect(CUSTOM_MODEL_VALUE).toBe('__custom__');
		expect(AUTO_MODEL).toBe('auto');
		expect(AUTO_MODEL_VALUE).toBe('__auto__');
	});

	it('keeps the auto sentinel distinct from any real catalog model id', () => {
		const allIds = PROVIDER_KINDS.flatMap((kind) => MODEL_CATALOG[kind].map((option) => option.id));
		expect(allIds).not.toContain(AUTO_MODEL_VALUE);
		expect(allIds).not.toContain(CUSTOM_MODEL_VALUE);
	});
});
