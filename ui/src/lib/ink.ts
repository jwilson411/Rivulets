// Agent -> print ink assignment (design: "agents as process inks").
// Each agent that speaks in a rivulet is assigned an ink in order of first
// appearance — cyan, magenta, yellow, then repeating. Humans always print in
// the fixed black "ink" plate rather than joining the cycle.

export type AgentInk = 'cyan' | 'magenta' | 'yellow';

const INK_ORDER: AgentInk[] = ['cyan', 'magenta', 'yellow'];

export function agentInk(index: number): AgentInk {
	return INK_ORDER[index % INK_ORDER.length];
}

export const INK_SWATCH: Record<AgentInk, string> = {
	cyan: 'bg-agent-cyan-500',
	magenta: 'bg-agent-magenta-500',
	yellow: 'bg-agent-yellow-500'
};

export const INK_AVATAR: Record<AgentInk, string> = {
	cyan: 'bg-agent-cyan-600 text-neutral-100 shadow-[2px_2px_0_rgba(56,166,207,.4)]',
	magenta: 'bg-agent-magenta-600 text-neutral-100 shadow-[2px_2px_0_rgba(216,32,113,.35)]',
	yellow: 'bg-agent-yellow-500 text-ink shadow-[2px_2px_0_rgba(237,187,0,.45)]'
};

export const INK_SPINE: Record<AgentInk, string> = {
	cyan: 'bg-agent-cyan-500',
	magenta: 'bg-agent-magenta-500',
	yellow: 'bg-agent-yellow-500'
};

export const INK_NAME_TEXT: Record<AgentInk, string> = {
	cyan: 'text-agent-cyan-700 dark:text-agent-cyan-400',
	magenta: 'text-agent-magenta-700 dark:text-agent-magenta-400',
	yellow: 'text-agent-yellow-700 dark:text-agent-yellow-500'
};

export const HUMAN_AVATAR = 'bg-ink text-paper dark:bg-ink-dark dark:text-paper-dark';
export const HUMAN_SPINE = 'bg-neutral-300 dark:bg-neutral-700';
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
