import type { RoutingRule } from '$lib/api/agents';

// #406: composer copy used to say "Routes to {team}", which reads as
// "the team will answer this." Mentions and When to speak rules decide
// who actually does.

export function teamComposerHint(teamName: string): string {
	return `${teamName} answers when a rule or @mention matches`;
}

export function keywordList(rules: RoutingRule[]): string[] {
	const words: string[] = [];
	const seen = new Set<string>();
	for (const rule of rules) {
		if (rule.rule_type !== 'keyword' && rule.rule_type !== 'semantic') continue;
		let parsed: unknown = rule.pattern;
		try {
			parsed = JSON.parse(rule.pattern) as unknown;
		} catch {
			// Stored as a raw string — treat the whole pattern as one term.
		}
		const items = Array.isArray(parsed) ? parsed : [parsed];
		for (const item of items) {
			const text = String(item).trim();
			const key = text.toLowerCase();
			if (!text || seen.has(key)) continue;
			seen.add(key);
			words.push(text);
		}
	}
	return words;
}

export function extraSpeakRules(rules: RoutingRule[]): RoutingRule[] {
	return rules.filter((rule) => rule.rule_type === 'regex');
}

export function describeSpeakRule(rules: RoutingRule[]): string {
	if (rules.some((rule) => rule.rule_type === 'always')) return 'always';
	if (rules.length === 0) return 'no rule yet';
	if (rules.every((rule) => rule.rule_type === 'mention_only')) return 'only when @mentioned';
	const words = keywordList(rules);
	if (words.length > 0) {
		const shown = words.slice(0, 3);
		const extra = words.length > 3 ? '…' : '';
		return `keywords: ${shown.join(', ')}${extra}`;
	}
	return 'custom rule';
}

export function teamSpeakSummary(members: { name: string; rules: RoutingRule[] }[]): string {
	return members.map((member) => `${member.name} ${describeSpeakRule(member.rules)}`).join(' · ');
}
