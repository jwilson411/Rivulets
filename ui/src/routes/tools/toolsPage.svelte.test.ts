// Browser-mode component test (see agents/agentsPage.svelte.test.ts). This
// route depends on $lib/api/tools, plus the real (unmocked) $lib/api/client
// for the ApiError class used in instanceof checks (including the 501
// simple-mode-codegen-not-wired-up special case).

import { page } from 'vitest/browser';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render } from 'vitest-browser-svelte';
import ToolsPage from './+page.svelte';
import { tools, type Tool, type ToolVersion } from '$lib/api/tools';
import { ApiError } from '$lib/api/client';

vi.mock('$lib/api/tools', () => ({
	tools: {
		list: vi.fn(),
		get: vi.fn(),
		create: vi.fn(),
		update: vi.fn(),
		remove: vi.fn(),
		listVersions: vi.fn(),
		saveVersion: vi.fn(),
		rollback: vi.fn(),
		openEditor: vi.fn()
	}
}));

const builtinTool: Tool = {
	id: 'tool-builtin',
	name: 'web_search',
	description: 'Search the web',
	tool_type: 'builtin',
	source_path: null,
	sensitive: false,
	available: true
};

const customTool: Tool = {
	id: 'tool-custom',
	name: 'my_tool',
	description: 'Does a custom thing',
	tool_type: 'custom',
	source_path: '/tools/my_tool.py',
	sensitive: false,
	available: true
};

const customToolVersion: ToolVersion = {
	version: 1,
	source_code: 'def run(): pass',
	created_at: '2026-08-01T00:00:00Z'
};

afterEach(() => {
	vi.clearAllMocks();
});

describe('tools/+page.svelte', () => {
	it('lists tools, tagging custom tools and showing their version history', async () => {
		vi.mocked(tools.list).mockResolvedValue([builtinTool, customTool]);
		vi.mocked(tools.listVersions).mockResolvedValue([customToolVersion]);

		render(ToolsPage);

		await expect.element(page.getByText('web_search')).toBeInTheDocument();
		await expect.element(page.getByText('my_tool')).toBeInTheDocument();
		await expect.element(page.getByText('Version history')).toBeInTheDocument();
		await expect.element(page.getByText(/v1 —/)).toBeInTheDocument();
		// Only the custom tool gets a Delete action.
		await expect.element(page.getByRole('button', { name: 'Delete' })).toBeInTheDocument();
	});

	it('creates a tool in advanced mode without a prompt field', async () => {
		vi.mocked(tools.list).mockResolvedValueOnce([]).mockResolvedValueOnce([customTool]);
		vi.mocked(tools.listVersions).mockResolvedValue([]);
		vi.mocked(tools.create).mockResolvedValueOnce(customTool);

		render(ToolsPage);
		await expect.element(page.getByPlaceholder('Tool source code')).not.toBeInTheDocument();

		await page.getByPlaceholder('Name').fill('my_tool');
		await page.getByPlaceholder('Description').fill('Does a custom thing');
		await page.getByRole('button', { name: 'Create tool' }).click();

		expect(tools.create).toHaveBeenCalledWith({
			name: 'my_tool',
			description: 'Does a custom thing',
			mode: 'advanced'
		});
	});

	it('switches to simple mode, requires a prompt, and includes it in the create call', async () => {
		vi.mocked(tools.list).mockResolvedValue([]);
		vi.mocked(tools.create).mockResolvedValueOnce(customTool);

		render(ToolsPage);

		await page.getByRole('button', { name: 'Simple mode' }).click();
		await expect
			.element(page.getByRole('button', { name: 'Simple mode' }))
			.toHaveAttribute('aria-pressed', 'true');

		await page.getByPlaceholder('Name').fill('my_tool');
		await page.getByPlaceholder('Description').fill('Does a custom thing');
		// Empty prompt should block submission.
		await page.getByRole('button', { name: 'Create tool' }).click();
		expect(tools.create).not.toHaveBeenCalled();

		await page
			.getByPlaceholder(/Describe what the tool should do/)
			.fill('Fetch the weather for a city');
		await page.getByRole('button', { name: 'Create tool' }).click();

		expect(tools.create).toHaveBeenCalledWith({
			name: 'my_tool',
			description: 'Does a custom thing',
			mode: 'simple',
			prompt: 'Fetch the weather for a city'
		});
	});

	it('shows the simple-mode-not-wired-up message on a 501 response', async () => {
		vi.mocked(tools.list).mockResolvedValue([]);
		vi.mocked(tools.create).mockRejectedValueOnce(new ApiError(501, 'not implemented'));

		render(ToolsPage);
		await page.getByRole('button', { name: 'Simple mode' }).click();
		await page.getByPlaceholder('Name').fill('my_tool');
		await page.getByPlaceholder('Description').fill('desc');
		await page.getByPlaceholder(/Describe what the tool should do/).fill('do a thing');
		await page.getByRole('button', { name: 'Create tool' }).click();

		await expect
			.element(
				page.getByText(
					'Simple-mode codegen isn’t wired up on this server yet — use Advanced Mode to create an empty tool and write it by hand.'
				)
			)
			.toBeInTheDocument();
	});

	it('deletes a custom tool via tools.remove', async () => {
		vi.mocked(tools.list).mockResolvedValueOnce([customTool]).mockResolvedValueOnce([]);
		vi.mocked(tools.listVersions).mockResolvedValue([customToolVersion]);
		vi.mocked(tools.remove).mockResolvedValueOnce(undefined);

		render(ToolsPage);
		await expect.element(page.getByText('my_tool')).toBeInTheDocument();

		await page.getByRole('button', { name: 'Delete' }).click();

		expect(tools.remove).toHaveBeenCalledWith('tool-custom');
		await expect.element(page.getByText('my_tool')).not.toBeInTheDocument();
	});

	it('shows an error when tools fail to load', async () => {
		vi.mocked(tools.list).mockRejectedValueOnce(new Error('Failed to load tools'));

		render(ToolsPage);

		await expect.element(page.getByText('Failed to load tools')).toBeInTheDocument();
	});

	it('does nothing when Create tool is submitted with an empty name and description', async () => {
		vi.mocked(tools.list).mockResolvedValue([]);

		render(ToolsPage);
		await expect.element(page.getByText('No tools yet — add one above.')).toBeInTheDocument();

		await page.getByRole('button', { name: 'Create tool' }).click();

		expect(tools.create).not.toHaveBeenCalled();
	});

	it('shows a generic error message when creating a tool fails with a non-ApiError', async () => {
		vi.mocked(tools.list).mockResolvedValue([]);
		vi.mocked(tools.create).mockRejectedValueOnce(new Error('network down'));

		render(ToolsPage);
		await page.getByPlaceholder('Name').fill('my_tool');
		await page.getByPlaceholder('Description').fill('Does a custom thing');
		await page.getByRole('button', { name: 'Create tool' }).click();

		await expect.element(page.getByText('Failed to create tool')).toBeInTheDocument();
	});

	it('toggles the creation mode back to advanced after switching to simple', async () => {
		vi.mocked(tools.list).mockResolvedValue([]);

		render(ToolsPage);
		await page.getByRole('button', { name: 'Simple mode' }).click();
		await expect
			.element(page.getByPlaceholder(/Describe what the tool should do/))
			.toBeInTheDocument();

		await page.getByRole('button', { name: 'Advanced mode' }).click();

		await expect
			.element(page.getByRole('button', { name: 'Advanced mode' }))
			.toHaveAttribute('aria-pressed', 'true');
		await expect
			.element(page.getByPlaceholder(/Describe what the tool should do/))
			.not.toBeInTheDocument();
	});

	it('shows an unavailable badge for a tool marked unavailable', async () => {
		vi.mocked(tools.list).mockResolvedValue([{ ...builtinTool, available: false }]);

		render(ToolsPage);

		await expect.element(page.getByText('unavailable')).toBeInTheDocument();
	});

	it('opens the editor for a custom tool and shows the returned path', async () => {
		vi.mocked(tools.list).mockResolvedValue([customTool]);
		vi.mocked(tools.listVersions).mockResolvedValue([customToolVersion]);
		vi.mocked(tools.openEditor).mockResolvedValueOnce({ path: '/tools/my_tool.py' });

		render(ToolsPage);
		await expect.element(page.getByText('my_tool')).toBeInTheDocument();

		await page.getByRole('button', { name: 'Open in editor' }).click();

		expect(tools.openEditor).toHaveBeenCalledWith('tool-custom');
		await expect.element(page.getByText('/tools/my_tool.py')).toBeInTheDocument();
	});

	it('shows an error when opening the editor fails', async () => {
		vi.mocked(tools.list).mockResolvedValue([customTool]);
		vi.mocked(tools.listVersions).mockResolvedValue([customToolVersion]);
		vi.mocked(tools.openEditor).mockRejectedValueOnce(new ApiError(500, 'Failed to open editor'));

		render(ToolsPage);
		await expect.element(page.getByText('my_tool')).toBeInTheDocument();

		await page.getByRole('button', { name: 'Open in editor' }).click();

		await expect.element(page.getByText('Failed to open editor')).toBeInTheDocument();
	});

	it("saves a new version of a tool's source code", async () => {
		vi.mocked(tools.list).mockResolvedValue([customTool]);
		vi.mocked(tools.listVersions)
			.mockResolvedValueOnce([customToolVersion])
			.mockResolvedValueOnce([
				{ version: 2, source_code: 'def run(): return 1', created_at: '2026-08-02T00:00:00Z' },
				customToolVersion
			]);
		vi.mocked(tools.saveVersion).mockResolvedValueOnce({
			version: 2,
			source_code: 'def run(): return 1',
			created_at: '2026-08-02T00:00:00Z'
		});

		render(ToolsPage);
		await expect.element(page.getByText('my_tool')).toBeInTheDocument();

		await page.getByPlaceholder('Tool source code').fill('def run(): return 1');
		await page.getByRole('button', { name: 'Save version' }).click();

		expect(tools.saveVersion).toHaveBeenCalledWith('tool-custom', 'def run(): return 1');
		await expect.element(page.getByText(/v2 —/)).toBeInTheDocument();
	});

	it('shows an error when saving a version fails', async () => {
		vi.mocked(tools.list).mockResolvedValue([customTool]);
		vi.mocked(tools.listVersions).mockResolvedValue([customToolVersion]);
		vi.mocked(tools.saveVersion).mockRejectedValueOnce(new Error('Failed to save version'));

		render(ToolsPage);
		await expect.element(page.getByText('my_tool')).toBeInTheDocument();

		await page.getByRole('button', { name: 'Save version' }).click();

		await expect.element(page.getByText('Failed to save version')).toBeInTheDocument();
	});

	it('rolls back to a previous version and reseeds the draft with its source', async () => {
		vi.mocked(tools.list).mockResolvedValue([customTool]);
		vi.mocked(tools.listVersions).mockResolvedValue([customToolVersion]);
		vi.mocked(tools.rollback).mockResolvedValueOnce(customTool);

		render(ToolsPage);
		await expect.element(page.getByText('my_tool')).toBeInTheDocument();

		await page.getByRole('button', { name: 'Roll back' }).click();

		expect(tools.rollback).toHaveBeenCalledWith('tool-custom', 1);
		await expect.element(page.getByPlaceholder('Tool source code')).toHaveValue('def run(): pass');
	});

	it('shows an error when rolling back a version fails', async () => {
		vi.mocked(tools.list).mockResolvedValue([customTool]);
		vi.mocked(tools.listVersions).mockResolvedValue([customToolVersion]);
		vi.mocked(tools.rollback).mockRejectedValueOnce(new Error('Failed to roll back'));

		render(ToolsPage);
		await expect.element(page.getByText('my_tool')).toBeInTheDocument();

		await page.getByRole('button', { name: 'Roll back' }).click();

		await expect.element(page.getByText('Failed to roll back')).toBeInTheDocument();
	});

	it('shows an error when deleting a tool fails', async () => {
		vi.mocked(tools.list).mockResolvedValue([customTool]);
		vi.mocked(tools.listVersions).mockResolvedValue([customToolVersion]);
		vi.mocked(tools.remove).mockRejectedValueOnce(new Error('Failed to delete tool'));

		render(ToolsPage);
		await expect.element(page.getByText('my_tool')).toBeInTheDocument();

		await page.getByRole('button', { name: 'Delete' }).click();

		await expect.element(page.getByText('Failed to delete tool')).toBeInTheDocument();
	});

	it('filters the tool list by name via the search box', async () => {
		vi.mocked(tools.list).mockResolvedValue([builtinTool, customTool]);
		vi.mocked(tools.listVersions).mockResolvedValue([customToolVersion]);

		render(ToolsPage);
		await expect.element(page.getByText('web_search')).toBeInTheDocument();
		await expect.element(page.getByText('my_tool')).toBeInTheDocument();

		await page.getByPlaceholder('Search tools…').fill('my_');

		await expect.element(page.getByText('my_tool')).toBeInTheDocument();
		await expect.element(page.getByText('web_search')).not.toBeInTheDocument();
	});

	it('filters the tool list by type', async () => {
		vi.mocked(tools.list).mockResolvedValue([builtinTool, customTool]);
		vi.mocked(tools.listVersions).mockResolvedValue([customToolVersion]);

		render(ToolsPage);
		await expect.element(page.getByText('web_search')).toBeInTheDocument();
		await expect.element(page.getByText('my_tool')).toBeInTheDocument();

		await page.getByRole('combobox', { name: 'Type' }).selectOptions('custom');

		await expect.element(page.getByText('my_tool')).toBeInTheDocument();
		await expect.element(page.getByText('web_search')).not.toBeInTheDocument();
	});
});
