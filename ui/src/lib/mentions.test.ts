import { describe, expect, it } from 'vitest';
import {
	applyMention,
	filterMentionCandidates,
	mentionNamesOf,
	mentionQueryAt,
	type MentionCandidate
} from './mentions';

const assistant: MentionCandidate = { id: 'a1', name: 'Assistant', kind: 'agent' };
const researcher: MentionCandidate = { id: 'a2', name: 'Researcher', kind: 'agent' };
const justin: MentionCandidate = { id: 'h1', name: 'Justin', kind: 'human' };

describe('mentionQueryAt', () => {
	it('opens on a leading @', () => {
		expect(mentionQueryAt('@', 1)).toEqual({ start: 0, query: '' });
		expect(mentionQueryAt('@Ass', 4)).toEqual({ start: 0, query: 'Ass' });
	});

	it('opens after whitespace, not in the middle of a word', () => {
		expect(mentionQueryAt('hi @As', 6)).toEqual({ start: 3, query: 'As' });
		expect(mentionQueryAt('hello@As', 8)).toBeNull();
	});

	it('uses the cursor, not the whole draft', () => {
		expect(mentionQueryAt('hi @As there', 6)).toEqual({ start: 3, query: 'As' });
		expect(mentionQueryAt('hi @As there', 12)).toBeNull();
	});
});

describe('filterMentionCandidates', () => {
	const all = [assistant, researcher, justin];

	it('lists everyone when the query is empty', () => {
		expect(filterMentionCandidates(all, '').map((c) => c.name)).toEqual([
			'Assistant',
			'Justin',
			'Researcher'
		]);
	});

	it('matches case-insensitively by prefix, then substring', () => {
		expect(filterMentionCandidates(all, 'assist').map((c) => c.name)).toEqual(['Assistant']);
		expect(filterMentionCandidates(all, 'ASSIST').map((c) => c.name)).toEqual(['Assistant']);
		expect(filterMentionCandidates(all, 'search').map((c) => c.name)).toEqual(['Researcher']);
	});
});

describe('applyMention', () => {
	it('replaces the partial token with the exact display name', () => {
		expect(applyMention('@assist', 7, 'Assistant')).toEqual({
			text: '@Assistant ',
			cursor: 11
		});
		expect(applyMention('hi @As', 6, 'Assistant')).toEqual({
			text: 'hi @Assistant ',
			cursor: 14
		});
	});

	it('appends a mention when no token is open', () => {
		expect(applyMention('hello', 5, 'Assistant')).toEqual({
			text: 'hello @Assistant ',
			cursor: 17
		});
		expect(applyMention('', 0, 'Assistant')).toEqual({
			text: '@Assistant ',
			cursor: 11
		});
	});
});

describe('mentionNamesOf', () => {
	it('returns display names in list order', () => {
		expect(mentionNamesOf([assistant, justin])).toEqual(['Assistant', 'Justin']);
	});
});
