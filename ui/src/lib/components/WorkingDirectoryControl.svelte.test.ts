import { page } from 'vitest/browser';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render } from 'vitest-browser-svelte';
import WorkingDirectoryControl from './WorkingDirectoryControl.svelte';
import { settings } from '$lib/api/settings';

vi.mock('$lib/api/settings', () => ({
	settings: { listDirectories: vi.fn(), createDirectory: vi.fn() }
}));

afterEach(() => {
	vi.clearAllMocks();
});

describe('WorkingDirectoryControl.svelte', () => {
	it('shows the built-in sandbox when nothing is set', async () => {
		render(WorkingDirectoryControl, { onSave: vi.fn() });

		await expect.element(page.getByText('Built-in sandbox')).toBeInTheDocument();
		await expect.element(page.getByText('the default')).toBeInTheDocument();
	});

	it('shows an inherited path and a Change button when editable', async () => {
		render(WorkingDirectoryControl, {
			inheritedPath: '/Users/me/river',
			inheritedLabel: 'This channel',
			canEdit: true,
			onSave: vi.fn()
		});

		await expect.element(page.getByText('/Users/me/river')).toBeInTheDocument();
		await expect.element(page.getByText('This channel')).toBeInTheDocument();
		await expect.element(page.getByRole('button', { name: 'Change' })).toBeInTheDocument();
	});

	it('clears an override back to the inherited folder', async () => {
		const onSave = vi.fn();
		render(WorkingDirectoryControl, {
			storedPath: '/Users/me/stream',
			inheritedPath: '/Users/me/river',
			inheritedLabel: 'This channel',
			canEdit: true,
			onSave
		});

		await expect.element(page.getByText('This conversation')).toBeInTheDocument();
		await page.getByRole('button', { name: 'Use This channel' }).click();

		expect(onSave).toHaveBeenCalledWith(null);
	});

	it('labels a channel override as This channel, not This conversation', async () => {
		const onSave = vi.fn();
		render(WorkingDirectoryControl, {
			storedPath: '/Users/me/river',
			inheritedPath: '/Users/me/settings',
			inheritedLabel: 'Settings',
			overrideLabel: 'This channel',
			canEdit: true,
			onSave
		});

		await expect.element(page.getByText('This channel')).toBeInTheDocument();
		await expect.element(page.getByText('This conversation')).not.toBeInTheDocument();
		await page.getByRole('button', { name: 'Use Settings' }).click();

		expect(onSave).toHaveBeenCalledWith(null);
	});

	it('opens the folder picker from Choose', async () => {
		vi.mocked(settings.listDirectories).mockResolvedValue({
			path: '/Users/me',
			parent: '/',
			entries: []
		});
		const onSave = vi.fn();

		render(WorkingDirectoryControl, { canEdit: true, onSave });
		await page.getByRole('button', { name: 'Choose' }).click();

		await expect.element(page.getByText('Choose a folder')).toBeInTheDocument();
		await page.getByRole('button', { name: 'Use this folder' }).click();

		expect(onSave).toHaveBeenCalledWith('/Users/me');
	});

	it('surfaces an error under the control', async () => {
		render(WorkingDirectoryControl, { onSave: vi.fn(), error: 'Could not save.' });

		await expect.element(page.getByText('Could not save.')).toBeInTheDocument();
	});
});
