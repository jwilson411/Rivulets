import type { RoutingRule } from '$lib/api/agents';

// #406: composer copy used to say "Routes to {team}", which reads as
// "the team will answer this." Mentions and When to speak rules decide
// who actually does.

export function teamComposerHint(teamName: string): string {
	return `${teamName} answers when a rule or @mention matches`;
}

export function describeSpeakRule(rules: RoutingRule[]): string {
	if (rules.some((rule) => rule.rule_type === 'always')) return 'always';
	if (rules.length === 0) return 'no rule yet';
	if (rules.every((rule) => rule.rule_type === 'mention_only')) return 'only when @mentioned';
	const keyword = rules.find((rule) => rule.rule_type === 'keyword');
	if (keyword) {
		try {
			const words = JSON.parse(keyword.pattern) as unknown;
			if (Array.isArray(words) && words.length > 0) {
				const shown = words.slice(0, 3).map(String);
				const extra = words.length > 3 ? '…' : '';
				return `keywords: ${shown.join(', ')}${extra}`;
			}
		} catch {
			// Fall through to the generic keyword label.
		}
		return 'keywords';
	}
	return 'custom rule';
}

export function teamSpeakSummary(members: { name: string; rules: RoutingRule[] }[]): string {
	return members.map((member) => `${member.name} ${describeSpeakRule(member.rules)}`).join(' · ');
}
