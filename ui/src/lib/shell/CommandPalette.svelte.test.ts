import { page } from 'vitest/browser';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render } from 'vitest-browser-svelte';
import CommandPalette from './CommandPalette.svelte';
import { channels } from '$lib/api/channels';
import { workflows } from '$lib/api/workflows';
import { goto } from '$app/navigation';
import { approvalsBadge } from '$lib/approvalsBadge.svelte';

const authState = vi.hoisted(() => ({ grant: 'owner' as string | null }));

vi.mock('$app/navigation', () => ({ goto: vi.fn() }));
vi.mock('$app/paths', () => ({
	resolve: (path: string) => path
}));

vi.mock('$lib/api/auth.svelte', () => ({
	auth: {
		get grant() {
			return authState.grant;
		}
	}
}));

vi.mock('$lib/api/channels', () => ({
	channels: { list: vi.fn() }
}));
vi.mock('$lib/api/workflows', () => ({
	workflows: { list: vi.fn() }
}));

afterEach(() => {
	vi.clearAllMocks();
	authState.grant = 'owner';
});

describe('CommandPalette.svelte', () => {
	it('lists jump-to channels and owner actions', async () => {
		vi.mocked(channels.list).mockResolvedValue([
			{
				id: 'chan-1',
				name: 'general',
				description: null,
				team_id: null,
				position: 0,
				archived: false,
				working_directory: null,
				effective_working_directory: null
			}
		]);
		vi.mocked(workflows.list).mockResolvedValue([
			{
				id: 'wf-1',
				name: 'retry-check',
				description: null,
				published: true,
				on_failure_workflow_id: null,
				on_call_agent_id: null,
				created_at: '2026-01-01T00:00:00Z',
				updated_at: '2026-01-01T00:00:00Z'
			}
		]);

		render(CommandPalette, { onClose: vi.fn() });

		await expect.element(page.getByText('general', { exact: true })).toBeInTheDocument();
		await expect.element(page.getByText('/retry-check')).toBeInTheDocument();
		await expect.element(page.getByText('Open Providers')).toBeInTheDocument();
		await expect.element(page.getByText('Open Integrations')).toBeInTheDocument();
		await expect.element(page.getByText('New agent')).toBeInTheDocument();
	});

	it('hides owner-only commands for guests', async () => {
		authState.grant = 'invite';
		vi.mocked(channels.list).mockResolvedValue([]);
		vi.mocked(workflows.list).mockResolvedValue([]);

		render(CommandPalette, { onClose: vi.fn() });

		await expect.element(page.getByText('Open Settings')).toBeInTheDocument();
		await expect.element(page.getByText('Open Providers')).not.toBeInTheDocument();
		await expect.element(page.getByText('Open Integrations')).not.toBeInTheDocument();
		await expect.element(page.getByText('Open Sync')).not.toBeInTheDocument();
	});

	it('filters by query and navigates on click', async () => {
		vi.mocked(channels.list).mockResolvedValue([]);
		vi.mocked(workflows.list).mockResolvedValue([]);
		const onClose = vi.fn();

		render(CommandPalette, { onClose });
		await expect.element(page.getByText('New agent')).toBeInTheDocument();

		await page.getByLabelText('Command palette search').fill('agent');
		await expect.element(page.getByText('New agent')).toBeInTheDocument();
		await expect.element(page.getByText('Open Settings')).not.toBeInTheDocument();

		await page.getByText('New agent').click();
		expect(onClose).toHaveBeenCalledOnce();
		expect(goto).toHaveBeenCalledWith('/agents');
	});

	it('opens Settings → Integrations from the owner command (#471)', async () => {
		vi.mocked(channels.list).mockResolvedValue([]);
		vi.mocked(workflows.list).mockResolvedValue([]);
		const onClose = vi.fn();

		render(CommandPalette, { onClose });
		await expect.element(page.getByText('Open Integrations')).toBeInTheDocument();

		await page.getByLabelText('Command palette search').fill('gmail');
		await expect.element(page.getByText('Open Integrations')).toBeInTheDocument();
		await page.getByText('Open Integrations').click();

		expect(onClose).toHaveBeenCalledOnce();
		expect(goto).toHaveBeenCalledWith('/settings?tab=integrations');
	});

	it('closes on Escape', async () => {
		vi.mocked(channels.list).mockResolvedValue([]);
		vi.mocked(workflows.list).mockResolvedValue([]);
		const onClose = vi.fn();

		render(CommandPalette, { onClose });
		const input = page.getByLabelText('Command palette search');
		await expect.element(input).toBeInTheDocument();
		await input
			.element()
			.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));

		expect(onClose).toHaveBeenCalled();
	});

	it('shows the approvals badge count', async () => {
		vi.mocked(channels.list).mockResolvedValue([]);
		vi.mocked(workflows.list).mockResolvedValue([]);
		Object.defineProperty(approvalsBadge, 'count', { configurable: true, get: () => 3 });

		render(CommandPalette, { onClose: vi.fn() });

		await expect.element(page.getByText('Open Approvals')).toBeInTheDocument();
		await expect.element(page.getByText('3')).toBeInTheDocument();
	});
});
