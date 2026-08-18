import { page } from 'vitest/browser';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render } from 'vitest-browser-svelte';
import FolderPickerSheet from './FolderPickerSheet.svelte';
import { settings, type DirectoryListing } from '$lib/api/settings';

vi.mock('$lib/api/settings', () => ({
	settings: { listDirectories: vi.fn(), createDirectory: vi.fn() }
}));

afterEach(() => {
	vi.clearAllMocks();
});

const root: DirectoryListing = {
	path: '/Users/me',
	parent: '/',
	entries: [
		{ name: 'proj', path: '/Users/me/proj' },
		{ name: 'docs', path: '/Users/me/docs' }
	]
};

const proj: DirectoryListing = {
	path: '/Users/me/proj',
	parent: '/Users/me',
	entries: []
};

describe('FolderPickerSheet.svelte', () => {
	it('lists folders and selects the current path', async () => {
		vi.mocked(settings.listDirectories).mockResolvedValue(root);
		const onSelect = vi.fn();

		render(FolderPickerSheet, { onClose: vi.fn(), onSelect });

		await expect.element(page.getByText('proj')).toBeInTheDocument();
		await page.getByRole('button', { name: 'Use this folder' }).click();

		expect(onSelect).toHaveBeenCalledWith('/Users/me');
	});

	it('navigates into a child folder', async () => {
		vi.mocked(settings.listDirectories).mockResolvedValueOnce(root).mockResolvedValueOnce(proj);

		render(FolderPickerSheet, { onClose: vi.fn(), onSelect: vi.fn() });
		await expect.element(page.getByText('proj')).toBeInTheDocument();

		await page.getByRole('button', { name: 'proj' }).click();

		await expect
			.element(page.getByText('No folders here. Use this one, or create a new folder.'))
			.toBeInTheDocument();
		expect(settings.listDirectories).toHaveBeenCalledWith('/Users/me/proj');
	});

	it('creates a new folder from the current listing', async () => {
		vi.mocked(settings.listDirectories).mockResolvedValue(root);
		vi.mocked(settings.createDirectory).mockResolvedValue({
			path: '/Users/me/new',
			parent: '/Users/me',
			entries: []
		});

		render(FolderPickerSheet, { onClose: vi.fn(), onSelect: vi.fn() });
		await expect.element(page.getByText('proj')).toBeInTheDocument();

		await page.getByRole('button', { name: 'New folder' }).click();
		await page.getByLabelText('New folder name').fill('new');
		await page.getByRole('button', { name: 'Create' }).click();

		await expect.poll(() => vi.mocked(settings.createDirectory).mock.calls.length).toBe(1);
		expect(settings.createDirectory).toHaveBeenCalledWith('/Users/me', 'new');
	});

	it('shows an error when the listing fails', async () => {
		vi.mocked(settings.listDirectories).mockRejectedValue(new Error('denied'));

		render(FolderPickerSheet, { onClose: vi.fn(), onSelect: vi.fn() });

		await expect.element(page.getByText("Couldn't open that folder.")).toBeInTheDocument();
	});
});
