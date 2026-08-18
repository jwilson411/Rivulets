import { describe, expect, it } from 'vitest';
import {
	defaultChannelTeamId,
	describeSpeakRule,
	describeSpeakRulesList,
	keywordsFromRules,
	speakChoiceFromRules,
	isTeamEngaged,
	lockedTeamComposerHint,
	teamComposerHint,
	teamSpeakSummary
} from './teamRouting';
import type { RoutingRule } from '$lib/api/agents';

function rule(rule_type: RoutingRule['rule_type'], pattern = ''): RoutingRule {
	return { id: 'r1', rule_type, pattern, priority: 0 };
}

describe('defaultChannelTeamId', () => {
	it('prefers a team whose name includes starter', () => {
		expect(
			defaultChannelTeamId([
				{ id: 't-test', name: 'Test Team' },
				{ id: 't-starter', name: 'Starter Team' }
			])
		).toBe('t-starter');
	});

	it('falls back to the first team, then null', () => {
		expect(defaultChannelTeamId([{ id: 't-test', name: 'Test Team' }])).toBe('t-test');
		expect(defaultChannelTeamId([])).toBeNull();
	});
});

describe('teamComposerHint', () => {
	it('says Assistant coordinates the team via handoff or mention', () => {
		expect(teamComposerHint('Test Team')).toContain('Assistant coordinates Test Team');
		expect(teamComposerHint('Test Team')).toContain('handoff');
	});
});

describe('lockedTeamComposerHint', () => {
	it('says Assistant is coordinating', () => {
		expect(lockedTeamComposerHint()).toContain('Assistant is coordinating');
	});
});

describe('isTeamEngaged', () => {
	it('is locked until a handoff or engage marker appears', () => {
		expect(isTeamEngaged([{ content_type: 'text' }])).toBe(false);
		expect(isTeamEngaged([{ content_type: 'team_engaged' }])).toBe(true);
		expect(isTeamEngaged([{ content_type: 'handoff' }])).toBe(true);
	});
});

describe('describeSpeakRule', () => {
	it('prefers always over other stored rules', () => {
		expect(describeSpeakRule([rule('keyword', '["specialist"]'), rule('always')])).toBe('always');
	});

	it('labels mention-only and empty rules distinctly', () => {
		expect(describeSpeakRule([])).toBe('no rule yet');
		expect(describeSpeakRule([rule('mention_only')])).toBe('only when @mentioned');
	});

	it('summarizes keyword lists', () => {
		expect(describeSpeakRule([rule('keyword', '["retry", "eval"]')])).toBe('keywords: retry, eval');
	});

	it('merges every keyword row so later lists are not hidden (#410)', () => {
		expect(
			describeSpeakRule([
				{ id: 'r1', rule_type: 'keyword', pattern: '["draft", "rewrite"]', priority: 5 },
				{ id: 'r2', rule_type: 'keyword', pattern: '["prose"]', priority: 3 }
			])
		).toBe('keywords: draft, rewrite, prose');
	});
});

describe('speakChoiceFromRules', () => {
	it('maps empty and exclusive everyday rules onto the sheet radios', () => {
		expect(speakChoiceFromRules([])).toBe('mention_only');
		expect(speakChoiceFromRules([rule('always')])).toBe('always');
		expect(speakChoiceFromRules([rule('mention_only')])).toBe('mention_only');
		expect(speakChoiceFromRules([rule('keyword', '["retry"]')])).toBe('keyword');
	});

	it('treats mixed or generated rule sets as custom so Save cannot hide them', () => {
		expect(
			speakChoiceFromRules([
				rule('keyword', '["specialist", "expert"]'),
				{ id: 'r2', rule_type: 'semantic', pattern: '["help"]', priority: 1 }
			])
		).toBe('custom');
		expect(speakChoiceFromRules([rule('regex', '\\bticket-\\d+\\b')])).toBe('custom');
	});
});

describe('keywordsFromRules', () => {
	it('joins every keyword rule so the sheet does not drop later lists', () => {
		expect(
			keywordsFromRules([
				rule('keyword', '["specialist", "expert"]'),
				{ id: 'r2', rule_type: 'keyword', pattern: '["help"]', priority: 1 }
			])
		).toBe('specialist, expert, help');
	});
});

describe('describeSpeakRulesList', () => {
	it('lists each stored rule in priority order', () => {
		expect(
			describeSpeakRulesList([
				{ id: 'r1', rule_type: 'keyword', pattern: '["help"]', priority: 1 },
				{ id: 'r2', rule_type: 'semantic', pattern: '["write prose"]', priority: 5 }
			])
		).toEqual(['When the message is about write prose', 'When the message includes help']);
	});

	it('includes a leftover generated regex so it is not hidden behind rule[0] (#410)', () => {
		expect(
			describeSpeakRulesList([
				{ id: 'r1', rule_type: 'regex', pattern: 'https?://\\S+', priority: 8 },
				{ id: 'r2', rule_type: 'keyword', pattern: '["draft"]', priority: 5 }
			])
		).toEqual(['When the message matches https?://\\S+', 'When the message includes draft']);
	});
});

describe('teamSpeakSummary', () => {
	it('joins each teammate with their When to speak label', () => {
		expect(
			teamSpeakSummary([
				{ name: 'Assistant', rules: [rule('always')] },
				{ name: 'Coder', rules: [rule('mention_only')] }
			])
		).toBe('Assistant always · Coder only when @mentioned');
	});
});
