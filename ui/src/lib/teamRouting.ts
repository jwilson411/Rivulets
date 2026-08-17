import type { RoutingRule } from '$lib/api/agents';

// #406: composer copy used to say "Routes to {team}", which reads as
// "the team will answer this." Mentions and When to speak rules decide
// who actually does.

// #409: the agent sheet radios can only represent one exclusive everyday
// choice. Generated keyword + semantic + regex sets are "custom" — show
// them honestly and don't replace them unless the user picks a simple
// choice on purpose.
export type SpeakChoice = 'always' | 'mention_only' | 'keyword' | 'custom';

export function teamComposerHint(teamName: string): string {
	return `${teamName} answers when a rule or @mention matches`;
}

export function parseRulePhrases(pattern: string): string[] {
	try {
		const parsed = JSON.parse(pattern) as unknown;
		if (Array.isArray(parsed)) return parsed.map(String).filter((word) => word.trim() !== '');
	} catch {
		// Stored as a raw string (regex, or a keyword rule written by hand).
	}
	return pattern.trim() === '' ? [] : [pattern];
}

export function speakChoiceFromRules(rules: RoutingRule[]): SpeakChoice {
	if (rules.length === 0) return 'mention_only';
	if (rules.every((rule) => rule.rule_type === 'always')) return 'always';
	if (rules.every((rule) => rule.rule_type === 'mention_only')) return 'mention_only';
	if (rules.every((rule) => rule.rule_type === 'keyword')) return 'keyword';
	return 'custom';
}

export function keywordsFromRules(rules: RoutingRule[]): string {
	return rules
		.filter((rule) => rule.rule_type === 'keyword')
		.flatMap((rule) => parseRulePhrases(rule.pattern))
		.join(', ');
}

export function describeSpeakRulesList(rules: RoutingRule[]): string[] {
	return [...rules]
		.sort((a, b) => b.priority - a.priority)
		.map((rule) => {
			const phrases = parseRulePhrases(rule.pattern).join(', ');
			switch (rule.rule_type) {
				case 'always':
					return 'Always';
				case 'mention_only':
					return 'Only when mentioned';
				case 'keyword':
					return phrases ? `When the message includes ${phrases}` : 'Keywords';
				case 'semantic':
					return phrases ? `When the message is about ${phrases}` : 'Similar meaning';
				case 'regex':
					return rule.pattern ? `When the message matches ${rule.pattern}` : 'A pattern';
			}
		});
}

export function describeSpeakRule(rules: RoutingRule[]): string {
	if (rules.some((rule) => rule.rule_type === 'always')) return 'always';
	if (rules.length === 0) return 'no rule yet';
	if (rules.every((rule) => rule.rule_type === 'mention_only')) return 'only when @mentioned';
	const keyword = rules.find((rule) => rule.rule_type === 'keyword');
	if (keyword) {
		const words = parseRulePhrases(keyword.pattern);
		if (words.length > 0) {
			const shown = words.slice(0, 3);
			const extra = words.length > 3 ? '…' : '';
			return `keywords: ${shown.join(', ')}${extra}`;
		}
		return 'keywords';
	}
	return 'custom rule';
}

export function teamSpeakSummary(members: { name: string; rules: RoutingRule[] }[]): string {
	return members.map((member) => `${member.name} ${describeSpeakRule(member.rules)}`).join(' · ');
}
