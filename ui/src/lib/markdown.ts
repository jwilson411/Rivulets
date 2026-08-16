// Minimal markdown renderer for message content (10-constraints.md: message
// content is markdown — render it; 06-screens.md: "headings, lists, code,
// links. Keep it readable, not a blog"). Hand-rolled instead of a dependency
// so everything that reaches innerHTML provably passes through escapeHtml
// first — the input is chat text written by humans and agents, not trusted
// markup.

// Built via fromCharCode so no raw NUL byte sits in this source file.
const NUL = String.fromCharCode(0);
const PLACEHOLDER_RE = new RegExp(`${NUL}(\\d+)${NUL}`, 'g');

function escapeHtml(text: string): string {
	return text
		.replaceAll('&', '&amp;')
		.replaceAll('<', '&lt;')
		.replaceAll('>', '&gt;')
		.replaceAll('"', '&quot;')
		.replaceAll("'", '&#39;');
}

// Inline transforms run on already-escaped text. Inline code is extracted
// into NUL-delimited placeholders first so `**not bold**` inside backticks
// stays literal — a NUL can't appear in user text that survived escaping.
function renderInline(text: string): string {
	const codeSpans: string[] = [];
	let out = escapeHtml(text).replace(/`([^`]+)`/g, (_, code: string) => {
		codeSpans.push(`<code>${code}</code>`);
		return NUL + (codeSpans.length - 1) + NUL;
	});

	// Links before emphasis so emphasis inside link text still renders.
	// http(s) only — anything else stays literal text.
	out = out.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, (_, label: string, url: string) => {
		return `<a href="${url}" target="_blank" rel="noreferrer noopener">${label}</a>`;
	});

	out = out.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
	out = out.replace(/(^|[^*])\*([^*\s][^*]*)\*/g, '$1<em>$2</em>');

	return out.replace(PLACEHOLDER_RE, (_, i: string) => codeSpans[Number(i)]);
}

export function renderMarkdown(src: string): string {
	// Strip any literal NULs up front so user text can never collide with
	// the inline-code placeholder scheme above.
	const lines = src.replaceAll(NUL, '').split('\n');
	const blocks: string[] = [];
	let i = 0;

	while (i < lines.length) {
		const line = lines[i];

		if (line.trim() === '') {
			i += 1;
			continue;
		}

		// Fenced code block
		if (line.trim().startsWith('```')) {
			const code: string[] = [];
			i += 1;
			while (i < lines.length && !lines[i].trim().startsWith('```')) {
				code.push(lines[i]);
				i += 1;
			}
			i += 1; // closing fence (or EOF)
			blocks.push(`<pre><code>${escapeHtml(code.join('\n'))}</code></pre>`);
			continue;
		}

		// Heading (levels 1–3 all render modestly — chat, not a blog)
		const heading = /^(#{1,3})\s+(.*)$/.exec(line);
		if (heading) {
			const level = heading[1].length;
			blocks.push(`<h${level}>${renderInline(heading[2])}</h${level}>`);
			i += 1;
			continue;
		}

		// Unordered list
		if (/^\s*[-*]\s+/.test(line)) {
			const items: string[] = [];
			while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
				items.push(`<li>${renderInline(lines[i].replace(/^\s*[-*]\s+/, ''))}</li>`);
				i += 1;
			}
			blocks.push(`<ul>${items.join('')}</ul>`);
			continue;
		}

		// Ordered list
		if (/^\s*\d+[.)]\s+/.test(line)) {
			const items: string[] = [];
			while (i < lines.length && /^\s*\d+[.)]\s+/.test(lines[i])) {
				items.push(`<li>${renderInline(lines[i].replace(/^\s*\d+[.)]\s+/, ''))}</li>`);
				i += 1;
			}
			blocks.push(`<ol>${items.join('')}</ol>`);
			continue;
		}

		// Blockquote
		if (/^\s*>\s?/.test(line)) {
			const quoted: string[] = [];
			while (i < lines.length && /^\s*>\s?/.test(lines[i])) {
				quoted.push(renderInline(lines[i].replace(/^\s*>\s?/, '')));
				i += 1;
			}
			blocks.push(`<blockquote>${quoted.join('<br>')}</blockquote>`);
			continue;
		}

		// Paragraph: consecutive non-blank, non-structural lines
		const para: string[] = [];
		while (
			i < lines.length &&
			lines[i].trim() !== '' &&
			!/^(#{1,3}\s|\s*```|\s*[-*]\s+|\s*\d+[.)]\s+|\s*>\s?)/.test(lines[i])
		) {
			para.push(renderInline(lines[i]));
			i += 1;
		}
		blocks.push(`<p>${para.join('<br>')}</p>`);
	}

	return blocks.join('');
}
