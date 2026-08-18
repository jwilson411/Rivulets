<script lang="ts">
	// Wide Stream icon set: simple 2px-stroke line icons drawn on a 24px
	// grid, taken from the redesign mockups. Hand-rolled (no CDN icon font)
	// because this app must work fully offline / local-first.
	let { name, class: className = '' }: { name: string; class?: string } = $props();
</script>

{#if name === 'logo'}
	<svg
		viewBox="0 0 24 24"
		width="24"
		height="24"
		fill="none"
		stroke="currentColor"
		stroke-width="2"
		stroke-linecap="round"
		class={className}
		aria-hidden="true"
	>
		<path d="M3 8c3 0 3 2.5 6 2.5S12 8 15 8s3 2.5 6 2.5" />
		<path d="M3 15c3 0 3 2.5 6 2.5s3-2.5 6-2.5 3 2.5 6 2.5" />
	</svg>
{:else if name === 'home'}
	<svg
		viewBox="0 0 24 24"
		width="24"
		height="24"
		fill="none"
		stroke="currentColor"
		stroke-width="2"
		stroke-linecap="round"
		stroke-linejoin="round"
		class={className}
		aria-hidden="true"
	>
		<path d="M3 10.5 12 3l9 7.5" />
		<path d="M5 9.5V21h14V9.5" />
	</svg>
{:else if name === 'hash'}
	<svg
		viewBox="0 0 24 24"
		width="24"
		height="24"
		fill="none"
		stroke="currentColor"
		stroke-width="2"
		stroke-linecap="round"
		class={className}
		aria-hidden="true"
	>
		<path d="M9 4 7 20M17 4l-2 16M4 9h17M3 15h17" />
	</svg>
{:else if name === 'inbox'}
	<svg
		viewBox="0 0 24 24"
		width="24"
		height="24"
		fill="none"
		stroke="currentColor"
		stroke-width="2"
		stroke-linecap="round"
		stroke-linejoin="round"
		class={className}
		aria-hidden="true"
	>
		<path d="M3 13h5l2 3h4l2-3h5" />
		<path d="M5.5 5h13L21 13v6H3v-6z" />
	</svg>
{:else if name === 'grid'}
	<svg
		viewBox="0 0 24 24"
		width="24"
		height="24"
		fill="none"
		stroke="currentColor"
		stroke-width="2"
		stroke-linecap="round"
		class={className}
		aria-hidden="true"
	>
		<rect x="4" y="4" width="7" height="7" rx="2" />
		<rect x="13" y="4" width="7" height="7" rx="2" />
		<rect x="4" y="13" width="7" height="7" rx="2" />
		<rect x="13" y="13" width="7" height="7" rx="2" />
	</svg>
{:else if name === 'search'}
	<svg
		viewBox="0 0 24 24"
		width="24"
		height="24"
		fill="none"
		stroke="currentColor"
		stroke-width="2"
		stroke-linecap="round"
		class={className}
		aria-hidden="true"
	>
		<circle cx="11" cy="11" r="7" />
		<path d="m20 20-3.5-3.5" />
	</svg>
{:else if name === 'plus'}
	<svg
		viewBox="0 0 24 24"
		width="24"
		height="24"
		fill="none"
		stroke="currentColor"
		stroke-width="2"
		stroke-linecap="round"
		class={className}
		aria-hidden="true"
	>
		<path d="M12 5v14M5 12h14" />
	</svg>
{:else if name === 'send'}
	<svg
		viewBox="0 0 24 24"
		width="24"
		height="24"
		fill="none"
		stroke="currentColor"
		stroke-width="2.2"
		stroke-linecap="round"
		stroke-linejoin="round"
		class={className}
		aria-hidden="true"
	>
		<path d="M12 19V5M5 12l7-7 7 7" />
	</svg>
{:else if name === 'attach'}
	<svg
		viewBox="0 0 24 24"
		width="24"
		height="24"
		fill="none"
		stroke="currentColor"
		stroke-width="2"
		stroke-linecap="round"
		stroke-linejoin="round"
		class={className}
		aria-hidden="true"
	>
		<path
			d="M21 12l-8.2 8.2a5 5 0 0 1-7.1-7.1L14 4.8a3.3 3.3 0 0 1 4.7 4.7L10.5 17.7a1.7 1.7 0 0 1-2.4-2.4L16 7.4"
		/>
	</svg>
{:else if name === 'back'}
	<svg
		viewBox="0 0 24 24"
		width="24"
		height="24"
		fill="none"
		stroke="currentColor"
		stroke-width="2.2"
		stroke-linecap="round"
		stroke-linejoin="round"
		class={className}
		aria-hidden="true"
	>
		<path d="M19 12H5M12 19l-7-7 7-7" />
	</svg>
{:else if name === 'chevron-down'}
	<svg
		viewBox="0 0 24 24"
		width="24"
		height="24"
		fill="none"
		stroke="currentColor"
		stroke-width="2.5"
		stroke-linecap="round"
		stroke-linejoin="round"
		class={className}
		aria-hidden="true"
	>
		<path d="m6 9 6 6 6-6" />
	</svg>
{:else if name === 'chevron-right'}
	<svg
		viewBox="0 0 24 24"
		width="24"
		height="24"
		fill="none"
		stroke="currentColor"
		stroke-width="2.5"
		stroke-linecap="round"
		stroke-linejoin="round"
		class={className}
		aria-hidden="true"
	>
		<path d="m9 6 6 6-6 6" />
	</svg>
{:else if name === 'close'}
	<svg
		viewBox="0 0 24 24"
		width="24"
		height="24"
		fill="none"
		stroke="currentColor"
		stroke-width="2"
		stroke-linecap="round"
		class={className}
		aria-hidden="true"
	>
		<path d="M6 6l12 12M18 6 6 18" />
	</svg>
{:else if name === 'check'}
	<svg
		viewBox="0 0 24 24"
		width="24"
		height="24"
		fill="none"
		stroke="currentColor"
		stroke-width="3"
		stroke-linecap="round"
		class={className}
		aria-hidden="true"
	>
		<path d="M4 12.5 9.5 18 20 6" />
	</svg>
{:else if name === 'copy'}
	<svg
		viewBox="0 0 24 24"
		width="24"
		height="24"
		fill="none"
		stroke="currentColor"
		stroke-width="2"
		stroke-linecap="round"
		class={className}
		aria-hidden="true"
	>
		<rect x="9" y="9" width="12" height="12" rx="3" />
		<path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
	</svg>
{:else if name === 'sun'}
	<svg
		viewBox="0 0 24 24"
		width="24"
		height="24"
		fill="none"
		stroke="currentColor"
		stroke-width="2"
		stroke-linecap="round"
		class={className}
		aria-hidden="true"
	>
		<circle cx="12" cy="12" r="4.5" />
		<path
			d="M12 2.5v2.5M12 19v2.5M2.5 12H5M19 12h2.5M4.9 4.9l1.8 1.8M17.3 17.3l1.8 1.8M19.1 4.9l-1.8 1.8M6.7 17.3l-1.8 1.8"
		/>
	</svg>
{:else if name === 'moon'}
	<svg
		viewBox="0 0 24 24"
		width="24"
		height="24"
		fill="none"
		stroke="currentColor"
		stroke-width="2"
		stroke-linecap="round"
		stroke-linejoin="round"
		class={className}
		aria-hidden="true"
	>
		<path d="M20 14.5A8.5 8.5 0 0 1 9.5 4 8.5 8.5 0 1 0 20 14.5Z" />
	</svg>
{:else if name === 'monitor'}
	<svg
		viewBox="0 0 24 24"
		width="24"
		height="24"
		fill="none"
		stroke="currentColor"
		stroke-width="2"
		stroke-linecap="round"
		class={className}
		aria-hidden="true"
	>
		<rect x="3" y="4.5" width="18" height="12.5" rx="2" />
		<path d="M9 20.5h6M12 17v3.5" />
	</svg>
{:else if name === 'folder'}
	<svg
		viewBox="0 0 24 24"
		width="24"
		height="24"
		fill="none"
		stroke="currentColor"
		stroke-width="2"
		stroke-linecap="round"
		stroke-linejoin="round"
		class={className}
		aria-hidden="true"
	>
		<path
			d="M3 7.5A2.5 2.5 0 0 1 5.5 5H9l2 2.5h7.5A2.5 2.5 0 0 1 21 10v7.5a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 17.5z"
		/>
	</svg>
{/if}
