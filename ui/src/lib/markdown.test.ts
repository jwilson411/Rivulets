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

describe('renderMarkdown blocks', () => {
	it('renders headings, emphasis, and links', () => {
		const html = renderMarkdown('# Title\n\nSee [docs](https://example.com) and **bold** *em*');
		expect(html).toContain('<h1>Title</h1>');
		expect(html).toContain('<strong>bold</strong>');
		expect(html).toContain('<em>em</em>');
		expect(html).toContain(
			'<a href="https://example.com" target="_blank" rel="noreferrer noopener">docs</a>'
		);
	});

	it('renders fenced code, lists, and blockquotes', () => {
		const html = renderMarkdown(
			'```\nconst x = 1\n```\n\n- one\n- two\n\n1. first\n2. second\n\n> quoted'
		);
		expect(html).toContain('<pre><code>const x = 1</code></pre>');
		expect(html).toContain('<ul><li>one</li><li>two</li></ul>');
		expect(html).toContain('<ol><li>first</li><li>second</li></ol>');
		expect(html).toContain('<blockquote>quoted</blockquote>');
	});

	it('escapes HTML inside fenced code and paragraphs', () => {
		const html = renderMarkdown('```\n<script>alert(1)</script>\n```\n\n<b>no</b>');
		expect(html).toContain('&lt;script&gt;alert(1)&lt;/script&gt;');
		expect(html).toContain('&lt;b&gt;no&lt;/b&gt;');
		expect(html).not.toContain('<script>');
	});
});
