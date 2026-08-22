// Browser-mode component test for Tools (06-screens.md → Tools, mockup
// 2i): grouped Built in / Yours / From MCP, plain-language availability
// pills, describe-first creation, and the owner-only source sheet.

import { page } from 'vitest/browser';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render } from 'vitest-browser-svelte';
import ToolsPage from './+page.svelte';
import { ApiError } from '$lib/api/client';
import { tools, type Tool, type ToolVersion } from '$lib/api/tools';

const authState = vi.hoisted(() => ({ grant: 'owner' }));

vi.mock('$lib/api/tools', () => ({
	tools: {
		list: vi.fn(),
		create: vi.fn(),
		generateCode: vi.fn(),
		remove: vi.fn(),
		listVersions: vi.fn(),
		saveVersion: vi.fn(),
		rollback: vi.fn(),
		openEditor: vi.fn()
	}
}));

vi.mock('$lib/api/auth.svelte', () => ({
	auth: {
		get grant() {
			return authState.grant;
		}
	}
}));

afterEach(() => {
	vi.clearAllMocks();
	authState.grant = 'owner';
});

const webSearch: Tool = {
	id: 'tool-1',
	name: 'web_search',
	description: 'Searches the web',
	tool_type: 'builtin',
	source_path: null,
	sensitive: false,
	required_scope: null,
	available: true,
	display_name: 'Web search',
	group: 'chat'
};

const executePython: Tool = {
	...webSearch,
	id: 'tool-2',
	name: 'execute_python',
	description: 'Runs code',
	sensitive: true,
	available: false,
	display_name: 'Execute Python',
	group: 'files'
};

const customTool: Tool = {
	...webSearch,
	id: 'tool-3',
	name: 'fetch_notes',
	description: 'Fetches notes',
	tool_type: 'custom',
	display_name: 'Fetch notes',
	group: 'custom'
};

const version: ToolVersion = {
	version: 1,
	source_code: 'def fetch_notes(): ...',
	created_at: '2026-01-01T00:00:00Z'
};

describe('tools/+page.svelte', () => {
	it('groups tools with plain-language availability pills', async () => {
		vi.mocked(tools.list).mockResolvedValue([webSearch, executePython, customTool]);

		render(ToolsPage);

		await expect.element(page.getByText('Built in')).toBeInTheDocument();
		await expect.element(page.getByText('Yours')).toBeInTheDocument();
		await expect.element(page.getByText('Web search')).toBeInTheDocument();
		await expect.element(page.getByText('web_search', { exact: true })).not.toBeInTheDocument();
		await expect.element(page.getByText('Available').first()).toBeInTheDocument();
		await expect.element(page.getByText('Sensitive')).toBeInTheDocument();
		await expect.element(page.getByText('Unavailable on this machine')).toBeInTheDocument();
	});

	it('leads tool creation with "describe what it should do", generating a draft to review', async () => {
		vi.mocked(tools.list).mockResolvedValue([]);
		vi.mocked(tools.generateCode).mockResolvedValueOnce({
			source_code: 'def fetch_notes(): ...'
		});

		render(ToolsPage);
		await page.getByRole('button', { name: 'New tool' }).click();

		await page.getByLabelText('Name').fill('fetch_notes');
		await page.getByLabelText('What agents see').fill('Fetches notes');
		await page.getByLabelText('Describe what it should do').fill('Read the notes file');
		await page.getByRole('button', { name: 'Generate code' }).click();

		expect(tools.generateCode).toHaveBeenCalledWith({
			name: 'fetch_notes',
			description: 'Fetches notes',
			prompt: 'Read the notes file'
		});
		// #517: a draft to review, not a created tool.
		expect(tools.create).not.toHaveBeenCalled();
		await expect
			.element(page.getByLabelText('Generated tool code'))
			.toHaveValue('def fetch_notes(): ...');
	});

	it('approving the reviewed draft creates the tool and saves the code (#517)', async () => {
		vi.mocked(tools.list).mockResolvedValue([]);
		vi.mocked(tools.generateCode).mockResolvedValueOnce({
			source_code: 'def fetch_notes(): ...'
		});
		vi.mocked(tools.create).mockResolvedValueOnce(customTool);
		vi.mocked(tools.saveVersion).mockResolvedValueOnce(version);

		render(ToolsPage);
		await page.getByRole('button', { name: 'New tool' }).click();
		await page.getByLabelText('Name').fill('fetch_notes');
		await page.getByLabelText('What agents see').fill('Fetches notes');
		await page.getByLabelText('Describe what it should do').fill('Read the notes file');
		await page.getByRole('button', { name: 'Generate code' }).click();

		// The draft is editable before approval.
		await page.getByLabelText('Generated tool code').fill('def fetch_notes(): return 2');
		await page.getByRole('button', { name: 'Approve and create' }).click();

		expect(tools.create).toHaveBeenCalledWith({
			name: 'fetch_notes',
			description: 'Fetches notes',
			mode: 'advanced'
		});
		expect(tools.saveVersion).toHaveBeenCalledWith('tool-3', 'def fetch_notes(): return 2');
	});

	it('offers pasting code instead, which creates an empty advanced tool', async () => {
		vi.mocked(tools.list).mockResolvedValue([]);
		vi.mocked(tools.create).mockResolvedValueOnce(customTool);

		render(ToolsPage);
		await page.getByRole('button', { name: 'New tool' }).click();
		await page.getByRole('button', { name: 'Paste code instead' }).click();

		await page.getByLabelText('Name').fill('fetch_notes');
		await page.getByLabelText('What agents see').fill('Fetches notes');
		await page.getByRole('button', { name: 'Create tool' }).click();

		expect(tools.create).toHaveBeenCalledWith({
			name: 'fetch_notes',
			description: 'Fetches notes',
			mode: 'advanced'
		});
	});

	it('offers a concrete next step when an older server still 501s codegen (#133)', async () => {
		vi.mocked(tools.list).mockResolvedValue([]);
		vi.mocked(tools.generateCode).mockRejectedValueOnce(new ApiError(501, 'not implemented'));

		render(ToolsPage);
		await page.getByRole('button', { name: 'New tool' }).click();
		await page.getByLabelText('Name').fill('fetch_notes');
		await page.getByLabelText('What agents see').fill('Fetches notes');
		await page.getByLabelText('Describe what it should do').fill('Read the notes file');
		await page.getByRole('button', { name: 'Generate code' }).click();

		await expect
			.element(
				page.getByText("Describing a tool isn't available on this machine yet.", { exact: false })
			)
			.toBeInTheDocument();
	});

	it("surfaces the server's own plain-language next step when generation fails (#517)", async () => {
		vi.mocked(tools.list).mockResolvedValue([]);
		vi.mocked(tools.generateCode).mockRejectedValueOnce(
			new ApiError(502, "Generating the code didn't work this time. Try again.")
		);

		render(ToolsPage);
		await page.getByRole('button', { name: 'New tool' }).click();
		await page.getByLabelText('Name').fill('fetch_notes');
		await page.getByLabelText('What agents see').fill('Fetches notes');
		await page.getByLabelText('Describe what it should do').fill('Read the notes file');
		await page.getByRole('button', { name: 'Generate code' }).click();

		await expect
			.element(page.getByText("Generating the code didn't work this time.", { exact: false }))
			.toBeInTheDocument();
	});

	it('opens a custom tool sheet with its source and saves a new version (owner)', async () => {
		vi.mocked(tools.list).mockResolvedValue([customTool]);
		vi.mocked(tools.listVersions).mockResolvedValue([version]);
		vi.mocked(tools.saveVersion).mockResolvedValueOnce(version);

		render(ToolsPage);
		await page.getByRole('button', { name: /Fetch notes/ }).click();

		const source = page.getByLabelText('Tool source code');
		await expect.element(source).toHaveValue('def fetch_notes(): ...');

		await source.fill('def fetch_notes(): return 2');
		await page.getByRole('button', { name: 'Save version' }).click();

		expect(tools.saveVersion).toHaveBeenCalledWith('tool-3', 'def fetch_notes(): return 2');
	});

	it('rolls back to a prior version from the sheet', async () => {
		vi.mocked(tools.list).mockResolvedValue([customTool]);
		vi.mocked(tools.listVersions).mockResolvedValue([version]);
		vi.mocked(tools.rollback).mockResolvedValueOnce(customTool);

		render(ToolsPage);
		await page.getByRole('button', { name: /Fetch notes/ }).click();
		await page.getByRole('button', { name: 'Roll back' }).click();

		expect(tools.rollback).toHaveBeenCalledWith('tool-3', 1);
	});

	it('deletes a custom tool behind a confirm sheet', async () => {
		vi.mocked(tools.list).mockResolvedValue([customTool]);
		vi.mocked(tools.listVersions).mockResolvedValue([version]);
		vi.mocked(tools.remove).mockResolvedValueOnce(undefined);

		render(ToolsPage);
		await page.getByRole('button', { name: /Fetch notes/ }).click();
		await page.getByRole('button', { name: 'Delete tool' }).click();

		expect(tools.remove).not.toHaveBeenCalled();
		await page.getByRole('button', { name: 'Delete tool' }).click();

		expect(tools.remove).toHaveBeenCalledWith('tool-3');
	});

	it('hides creation and the source sheet from guests (#321/#351)', async () => {
		authState.grant = 'invite';
		vi.mocked(tools.list).mockResolvedValue([customTool]);

		render(ToolsPage);
		await expect.element(page.getByText('Fetch notes')).toBeInTheDocument();

		await expect.element(page.getByRole('button', { name: 'New tool' })).not.toBeInTheDocument();
		// Custom rows aren't clickable buttons for a guest.
		await expect.element(page.getByRole('button', { name: /Fetch notes/ })).not.toBeInTheDocument();
	});

	it('shows a quiet error with retry when tools fail to load', async () => {
		vi.mocked(tools.list).mockRejectedValue(new Error('boom'));

		render(ToolsPage);

		await expect.element(page.getByText("Couldn't load tools.")).toBeInTheDocument();
		await expect.element(page.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
	});
});
