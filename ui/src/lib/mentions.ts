// Mention tokens match dispatch/engine.py's `_MENTION_RE`: `@` plus
// `[A-Za-z0-9_-]+`, compared to an agent display name case-insensitively.
// The picker exists so the composer inserts that exact name instead of
// leaving `@assist` as raw text that never dispatches.

export type MentionKind = 'agent' | 'human';

export interface MentionCandidate {
	id: string;
	name: string;
	kind: MentionKind;
}

export interface MentionQuery {
	start: number;
	query: string;
}

// @ must start a token (start of text, or after whitespace / opener).
// `hello@Assistant` stays an email-ish string, not a mention.
const MENTION_AT_RE = /(?:^|[\s([{])@([A-Za-z0-9_-]*)$/;

export function mentionQueryAt(text: string, cursor: number): MentionQuery | null {
	const before = text.slice(0, Math.max(0, cursor));
	if (!MENTION_AT_RE.test(before)) return null;
	return { start: before.lastIndexOf('@'), query: before.slice(before.lastIndexOf('@') + 1) };
}

export function filterMentionCandidates(
	candidates: MentionCandidate[],
	query: string
): MentionCandidate[] {
	const q = query.toLowerCase();
	const scored = candidates
		.map((candidate) => {
			const name = candidate.name.toLowerCase();
			if (q.length === 0 || name === q) return { candidate, score: 3 };
			if (name.startsWith(q)) return { candidate, score: 2 };
			if (name.includes(q)) return { candidate, score: 1 };
			return null;
		})
		.filter((row): row is { candidate: MentionCandidate; score: number } => row !== null)
		.sort((a, b) => b.score - a.score || a.candidate.name.localeCompare(b.candidate.name));
	return scored.map((row) => row.candidate);
}

export function applyMention(
	text: string,
	cursor: number,
	name: string
): { text: string; cursor: number } {
	const insertion = `@${name} `;
	const token = mentionQueryAt(text, cursor);
	if (!token) {
		const before = text.slice(0, cursor);
		const after = text.slice(cursor);
		const pad = before.length > 0 && !/\s$/.test(before) ? ' ' : '';
		const next = `${before}${pad}${insertion}${after}`;
		return { text: next, cursor: before.length + pad.length + insertion.length };
	}
	const next = `${text.slice(0, token.start)}${insertion}${text.slice(cursor)}`;
	return { text: next, cursor: token.start + insertion.length };
}

export function mentionNamesOf(candidates: MentionCandidate[]): string[] {
	return candidates.map((candidate) => candidate.name);
}
