import { describe, expect, it } from 'vitest';
import { renderMarkdown } from './markdown';

describe('renderMarkdown mentions', () => {
	it('highlights a resolved mention case-insensitively and leaves unknown tokens alone', () => {
		const html = renderMarkdown('@assistant ping @Nobody', ['Assistant']);
		expect(html).toContain('<span class="mention">@assistant</span> ping @Nobody');
	});

	it('does not highlight mentions inside inline code', () => {
		const html = renderMarkdown('see `@Assistant`', ['Assistant']);
		expect(html).toContain('<code>@Assistant</code>');
		expect(html).not.toContain('class="mention"');
	});

	it('escapes HTML in mention names before wrapping', () => {
		const html = renderMarkdown('@A&B hello', ['A&B']);
		expect(html).toContain('<span class="mention">@A&amp;B</span>');
	});
});
