import { describe, expect, it } from 'vitest';
import {
	describeSpeakRule,
	extraSpeakRules,
	keywordList,
	teamComposerHint,
	teamSpeakSummary
} from './teamRouting';
import type { RoutingRule } from '$lib/api/agents';

function rule(rule_type: RoutingRule['rule_type'], pattern = ''): RoutingRule {
	return { id: 'r1', rule_type, pattern, priority: 0 };
}

describe('teamComposerHint', () => {
	it('says the team answers only when a rule or mention matches', () => {
		expect(teamComposerHint('Test Team')).toBe(
			'Test Team answers when a rule or @mention matches'
		);
	});
});

describe('describeSpeakRule', () => {
	it('prefers always over other stored rules', () => {
		expect(describeSpeakRule([rule('keyword', '["specialist"]'), rule('always')])).toBe(
			'always'
		);
	});

	it('labels mention-only and empty rules distinctly', () => {
		expect(describeSpeakRule([])).toBe('no rule yet');
		expect(describeSpeakRule([rule('mention_only')])).toBe('only when @mentioned');
	});

	it('summarizes keyword lists', () => {
		expect(describeSpeakRule([rule('keyword', '["retry", "eval"]')])).toBe(
			'keywords: retry, eval'
		);
	});

	it('merges keyword and semantic rows so later rules are not hidden', () => {
		expect(
			keywordList([
				{ id: 'r1', rule_type: 'regex', pattern: 'https?://\\S+', priority: 8 },
				{ id: 'r2', rule_type: 'keyword', pattern: '["draft", "rewrite"]', priority: 5 },
				{ id: 'r3', rule_type: 'semantic', pattern: '["prose"]', priority: 3 }
			])
		).toEqual(['draft', 'rewrite', 'prose']);
		expect(
			extraSpeakRules([
				{ id: 'r1', rule_type: 'regex', pattern: 'https?://\\S+', priority: 8 },
				{ id: 'r2', rule_type: 'keyword', pattern: '["draft"]', priority: 5 }
			]).map((rule) => rule.pattern)
		).toEqual(['https?://\\S+']);
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
