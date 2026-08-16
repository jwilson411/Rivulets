// Agent identity inks (Wide Stream, 03-design-direction.md). Each agent
// that speaks in a rivulet is assigned an ink in order of first appearance
// — agent-a (green), agent-b (blue), agent-c (amber), then repeating.
// Humans always print in the fixed ink plate rather than joining the cycle.
// Identity is never color alone: an initial disc + name always show.

export type AgentInk = 'a' | 'b' | 'c';

const INK_ORDER: AgentInk[] = ['a', 'b', 'c'];

export function agentInk(index: number): AgentInk {
	return INK_ORDER[index % INK_ORDER.length];
}

/** Solid ink for dots and bars. */
export const INK_SWATCH: Record<AgentInk, string> = {
	a: 'bg-agent-a',
	b: 'bg-agent-b',
	c: 'bg-agent-c'
};

/** Rounded-xl initial disc, colored by ink. */
export const INK_AVATAR: Record<AgentInk, string> = {
	a: 'bg-agent-a text-white',
	b: 'bg-agent-b text-white',
	c: 'bg-agent-c text-white'
};

/** Soft tinted fill for an agent's message bubble. */
export const INK_BUBBLE: Record<AgentInk, string> = {
	a: 'bg-agent-a-soft dark:bg-agent-a-soft-dark',
	b: 'bg-agent-b-soft dark:bg-agent-b-soft-dark',
	c: 'bg-agent-c-soft dark:bg-agent-c-soft-dark'
};

export const INK_NAME_TEXT: Record<AgentInk, string> = {
	a: 'text-agent-a',
	b: 'text-agent-b',
	c: 'text-agent-c'
};

export const HUMAN_AVATAR = 'bg-ink text-paper dark:bg-ink-dark dark:text-paper-dark';
export const HUMAN_NAME_TEXT = 'text-ink dark:text-ink-dark';

export function initials(name: string): string {
	const trimmed = name.trim();
	return trimmed ? trimmed.charAt(0).toUpperCase() : '?';
}

/** Maps each distinct agent sender_id to its ink, in order of first appearance. */
export function agentInkMap(
	messages: { sender_type: string; sender_id: string | null }[]
): Map<string, AgentInk> {
	const map = new Map<string, AgentInk>();
	for (const m of messages) {
		if (m.sender_type !== 'agent' || !m.sender_id || map.has(m.sender_id)) continue;
		map.set(m.sender_id, agentInk(map.size));
	}
	return map;
}

/** Stable ink for an agent list (agents page, teams, channel headers):
 *  assigned by position in the given ordered list. */
export function agentInkByPosition(index: number): AgentInk {
	return agentInk(index);
}
