import { describe, expect, it } from 'vitest';
import {
	agentInk,
	agentInkMap,
	initials,
	INK_AVATAR,
	INK_BUBBLE,
	INK_NAME_TEXT,
	INK_SWATCH
} from './ink';

describe('agentInk', () => {
	it('cycles agent-a, agent-b, agent-c in order of index', () => {
		expect(agentInk(0)).toBe('a');
		expect(agentInk(1)).toBe('b');
		expect(agentInk(2)).toBe('c');
	});

	it('wraps back around after the third ink', () => {
		expect(agentInk(3)).toBe('a');
		expect(agentInk(4)).toBe('b');
		expect(agentInk(5)).toBe('c');
		expect(agentInk(7)).toBe('b');
	});
});

describe('ink style maps', () => {
	it('have an entry for every AgentInk value', () => {
		for (const ink of ['a', 'b', 'c'] as const) {
			expect(INK_SWATCH[ink]).toBeTypeOf('string');
			expect(INK_AVATAR[ink]).toBeTypeOf('string');
			expect(INK_BUBBLE[ink]).toBeTypeOf('string');
			expect(INK_NAME_TEXT[ink]).toBeTypeOf('string');
		}
	});
});

describe('initials', () => {
	it('returns the uppercased first character of a name', () => {
		expect(initials('alice')).toBe('A');
		expect(initials('bob smith')).toBe('B');
	});

	it('trims leading whitespace before taking the first character', () => {
		expect(initials('  carol')).toBe('C');
	});

	it('falls back to "?" for an empty or whitespace-only name', () => {
		expect(initials('')).toBe('?');
		expect(initials('   ')).toBe('?');
	});
});

describe('agentInkMap', () => {
	it('assigns inks to agent senders in order of first appearance', () => {
		const map = agentInkMap([
			{ sender_type: 'agent', sender_id: 'agent-2' },
			{ sender_type: 'human', sender_id: 'human-1' },
			{ sender_type: 'agent', sender_id: 'agent-1' },
			{ sender_type: 'agent', sender_id: 'agent-2' }
		]);

		expect(map.get('agent-2')).toBe('a');
		expect(map.get('agent-1')).toBe('b');
		expect(map.has('human-1')).toBe(false);
		expect(map.size).toBe(2);
	});

	it('ignores agent messages with a null sender_id', () => {
		const map = agentInkMap([{ sender_type: 'agent', sender_id: null }]);

		expect(map.size).toBe(0);
	});

	it('wraps the ink cycle for a fourth distinct agent', () => {
		const map = agentInkMap([
			{ sender_type: 'agent', sender_id: 'a1' },
			{ sender_type: 'agent', sender_id: 'a2' },
			{ sender_type: 'agent', sender_id: 'a3' },
			{ sender_type: 'agent', sender_id: 'a4' }
		]);

		expect(map.get('a4')).toBe('a');
	});
});
