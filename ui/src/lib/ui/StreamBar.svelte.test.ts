// Browser-mode component test for the Stream Bar: slash-command picker
// plus Slack-style @mention autocomplete (#408).

import { page } from 'vitest/browser';
import { describe, expect, it, vi } from 'vitest';
import { render } from 'vitest-browser-svelte';
import StreamBar from './StreamBar.svelte';
import type { Workflow } from '$lib/api/workflows';
import type { MentionCandidate } from '$lib/mentions';

const summarize: Workflow = {
	id: 'wf-1',
	name: 'summarize',
	description: 'Summarize the thread',
	published: true,
	on_failure_workflow_id: null,
	on_call_agent_id: null,
	created_at: new Date().toISOString(),
	updated_at: new Date().toISOString()
};

const draftWorkflow: Workflow = {
	...summarize,
	id: 'wf-2',
	name: 'drafty',
	description: null,
	published: false
};

const assistant: MentionCandidate = { id: 'agent-1', name: 'Assistant', kind: 'agent' };
const researcher: MentionCandidate = { id: 'agent-2', name: 'Researcher', kind: 'agent' };

describe('StreamBar.svelte', () => {
	it('opens a mention picker on @ and inserts the exact display name', async () => {
		const onSend = vi.fn(async () => true);
		render(StreamBar, {
			placeholder: 'Reply to this conversation…',
			mentionCandidates: [assistant, researcher],
			onSend
		});

		const input = page.getByPlaceholder('Reply to this conversation…');
		await input.fill('@');

		await expect.element(page.getByRole('option', { name: /@Assistant/ })).toBeInTheDocument();
		await expect.element(page.getByRole('option', { name: /@Researcher/ })).toBeInTheDocument();

		await page.getByRole('option', { name: /@Assistant/ }).click();

		await expect.element(input).toHaveValue('@Assistant ');
		expect(onSend).not.toHaveBeenCalled();
	});

	it('filters teammates as you type and Enter completes instead of sending', async () => {
		const onSend = vi.fn(async () => true);
		render(StreamBar, {
			placeholder: 'Reply to this conversation…',
			mentionCandidates: [assistant, researcher],
			onSend
		});

		const input = page.getByPlaceholder('Reply to this conversation…');
		await input.fill('@ass');

		await expect.element(page.getByRole('option', { name: /@Assistant/ })).toBeInTheDocument();
		await expect.element(page.getByRole('option', { name: /@Researcher/ })).not.toBeInTheDocument();

		await input.click();
		await (input.element() as HTMLTextAreaElement).dispatchEvent(
			new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })
		);

		await expect.element(input).toHaveValue('@Assistant ');
		expect(onSend).not.toHaveBeenCalled();
	});

	it('still lists published slash commands when the draft is a / token', async () => {
		render(StreamBar, {
			placeholder: 'Start a conversation…',
			slashWorkflows: [summarize, draftWorkflow],
			onSend: async () => true
		});

		await page.getByPlaceholder('Start a conversation…').fill('/');

		await expect.element(page.getByRole('menuitem', { name: /\/summarize/ })).toBeInTheDocument();
		await expect
			.element(page.getByRole('menuitem', { name: /Draft — publish to run it/ }))
			.toBeInTheDocument();
	});
});
