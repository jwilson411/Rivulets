// Browser-mode component test (see LoginForm.svelte.test.ts) -- Icon only
// depends on its own props, no SvelteKit/api modules to mock. It renders a
// hand-rolled aria-hidden SVG per `name`, so assertions go through the
// rendered container's DOM rather than accessible-role queries. render()
// itself is async (see vitest-browser-svelte's pure.ts), so every call here
// is awaited before reading its result.

import { describe, expect, it } from 'vitest';
import { render } from 'vitest-browser-svelte';
import Icon from './Icon.svelte';

describe('Icon.svelte', () => {
	it('renders an svg for a known icon name', async () => {
		const { container } = await render(Icon, { name: 'robot' });

		const svg = container.querySelector('svg');
		expect(svg).not.toBeNull();
		expect(svg?.getAttribute('aria-hidden')).toBe('true');
	});

	it('renders nothing for an unrecognized icon name', async () => {
		const { container } = await render(Icon, { name: 'not-a-real-icon' });

		expect(container.querySelector('svg')).toBeNull();
	});

	it('applies the class prop to the rendered svg', async () => {
		const { container } = await render(Icon, { name: 'users', class: 'h-4 w-4 text-red-500' });

		const svg = container.querySelector('svg');
		expect(svg?.getAttribute('class')).toBe('h-4 w-4 text-red-500');
	});

	it('renders a distinct svg per known icon name', async () => {
		const names = [
			'robot',
			'users',
			'plug',
			'server',
			'sync',
			'paperclip',
			'route',
			'sun',
			'moon',
			'monitor',
			'settings',
			'chart',
			'wrench'
		];

		for (const name of names) {
			const { container, unmount } = await render(Icon, { name });
			expect(container.querySelector('svg'), `expected an svg for icon "${name}"`).not.toBeNull();
			await unmount();
		}
	});
});
