<script lang="ts">
	// Hand-rolled line icons — avoids depending on a CDN icon font, which
	// would break silently in this app's offline / local-first environment.
	let { name, class: className = '' }: { name: string; class?: string } = $props();
</script>

{#if name === 'robot'}
	<svg viewBox="0 0 20 20" fill="none" class={className} aria-hidden="true">
		<rect x="4" y="7" width="12" height="9" rx="1.5" stroke="currentColor" stroke-width="1.5" />
		<circle cx="7.5" cy="11.5" r="1" fill="currentColor" />
		<circle cx="12.5" cy="11.5" r="1" fill="currentColor" />
		<path
			d="M10 7V4M10 4a1.2 1.2 0 1 0 0-2.4A1.2 1.2 0 0 0 10 4Z"
			stroke="currentColor"
			stroke-width="1.5"
		/>
		<path d="M2 10.5h2M16 10.5h2" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" />
	</svg>
{:else if name === 'users'}
	<svg viewBox="0 0 20 20" fill="none" class={className} aria-hidden="true">
		<circle cx="7" cy="7" r="2.6" stroke="currentColor" stroke-width="1.5" />
		<path
			d="M2.5 16c.6-3 2.2-4.5 4.5-4.5s3.9 1.5 4.5 4.5"
			stroke="currentColor"
			stroke-width="1.5"
			stroke-linecap="round"
		/>
		<circle cx="14" cy="6.5" r="2.1" stroke="currentColor" stroke-width="1.5" />
		<path
			d="M12.7 11.7c1.8.3 3 1.5 3.6 4.3"
			stroke="currentColor"
			stroke-width="1.5"
			stroke-linecap="round"
		/>
	</svg>
{:else if name === 'plug'}
	<svg viewBox="0 0 20 20" fill="none" class={className} aria-hidden="true">
		<path d="M7 2.5v4M13 2.5v4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" />
		<rect x="5.5" y="6" width="9" height="5" rx="1.5" stroke="currentColor" stroke-width="1.5" />
		<path
			d="M10 11v2.5a3 3 0 0 1-3 3H5"
			stroke="currentColor"
			stroke-width="1.5"
			stroke-linecap="round"
		/>
	</svg>
{:else if name === 'server'}
	<svg viewBox="0 0 20 20" fill="none" class={className} aria-hidden="true">
		<rect x="3" y="3.5" width="14" height="5" rx="1.2" stroke="currentColor" stroke-width="1.5" />
		<rect x="3" y="11.5" width="14" height="5" rx="1.2" stroke="currentColor" stroke-width="1.5" />
		<circle cx="6" cy="6" r=".8" fill="currentColor" />
		<circle cx="6" cy="14" r=".8" fill="currentColor" />
	</svg>
{:else if name === 'sync'}
	<svg viewBox="0 0 20 20" fill="none" class={className} aria-hidden="true">
		<path
			d="M4 9a6 6 0 0 1 10.2-4.3M16 9a6 6 0 0 1-10.2 4.3"
			stroke="currentColor"
			stroke-width="1.5"
			stroke-linecap="round"
		/>
		<path
			d="M14.5 2.5v3h-3M5.5 17.5v-3h3"
			stroke="currentColor"
			stroke-width="1.5"
			stroke-linecap="round"
			stroke-linejoin="round"
		/>
	</svg>
{:else if name === 'paperclip'}
	<svg viewBox="0 0 20 20" fill="none" class={className} aria-hidden="true">
		<path
			d="M13.5 7.5 8 13a2.5 2.5 0 0 1-3.5-3.5l6-6a4 4 0 0 1 5.7 5.7l-6.4 6.4a1.5 1.5 0 0 1-2.1-2.1l5.7-5.7"
			stroke="currentColor"
			stroke-width="1.4"
			stroke-linecap="round"
			stroke-linejoin="round"
		/>
	</svg>
{:else if name === 'route'}
	<svg viewBox="0 0 20 20" fill="none" class={className} aria-hidden="true">
		<path
			d="M3 5h6.5a3 3 0 0 1 3 3v4a3 3 0 0 0 3 3H17"
			stroke="currentColor"
			stroke-width="1.5"
			stroke-linecap="round"
		/>
		<path
			d="M14 12.5 17 15l-3 2.5"
			stroke="currentColor"
			stroke-width="1.5"
			stroke-linecap="round"
			stroke-linejoin="round"
		/>
	</svg>
{:else if name === 'sun'}
	<svg viewBox="0 0 20 20" fill="none" class={className} aria-hidden="true">
		<circle cx="10" cy="10" r="3.2" stroke="currentColor" stroke-width="1.5" />
		<path
			d="M10 2.5v2M10 15.5v2M17.5 10h-2M4.5 10h-2M15.3 4.7l-1.4 1.4M6.1 13.9l-1.4 1.4M15.3 15.3l-1.4-1.4M6.1 6.1 4.7 4.7"
			stroke="currentColor"
			stroke-width="1.5"
			stroke-linecap="round"
		/>
	</svg>
{:else if name === 'moon'}
	<svg viewBox="0 0 20 20" fill="none" class={className} aria-hidden="true">
		<path
			d="M16.5 12.3A6.7 6.7 0 0 1 7.7 3.5a6.7 6.7 0 1 0 8.8 8.8Z"
			stroke="currentColor"
			stroke-width="1.5"
			stroke-linejoin="round"
		/>
	</svg>
{:else if name === 'monitor'}
	<svg viewBox="0 0 20 20" fill="none" class={className} aria-hidden="true">
		<rect x="2.5" y="4" width="15" height="10" rx="1.2" stroke="currentColor" stroke-width="1.5" />
		<path d="M7 17h6M10 14v3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" />
	</svg>
{:else if name === 'settings'}
	<svg viewBox="0 0 20 20" fill="none" class={className} aria-hidden="true">
		<path
			d="M3 5.5h8M14.5 5.5H17M3 14.5h4.5M11 14.5H17"
			stroke="currentColor"
			stroke-width="1.5"
			stroke-linecap="round"
		/>
		<circle cx="11" cy="5.5" r="1.8" stroke="currentColor" stroke-width="1.5" />
		<circle cx="9" cy="14.5" r="1.8" stroke="currentColor" stroke-width="1.5" />
	</svg>
{:else if name === 'chart'}
	<svg viewBox="0 0 20 20" fill="none" class={className} aria-hidden="true">
		<path
			d="M3 17V3M3 17h14"
			stroke="currentColor"
			stroke-width="1.5"
			stroke-linecap="round"
			stroke-linejoin="round"
		/>
		<path
			d="M6.5 14v-4M10.5 14V6M14.5 14v-7"
			stroke="currentColor"
			stroke-width="1.5"
			stroke-linecap="round"
		/>
	</svg>
{:else if name === 'chevron'}
	<svg viewBox="0 0 20 20" fill="none" class={className} aria-hidden="true">
		<path
			d="M7 4l6 6-6 6"
			stroke="currentColor"
			stroke-width="1.5"
			stroke-linecap="round"
			stroke-linejoin="round"
		/>
	</svg>
{:else if name === 'user-plus'}
	<svg viewBox="0 0 20 20" fill="none" class={className} aria-hidden="true">
		<circle cx="7.5" cy="6.5" r="2.8" stroke="currentColor" stroke-width="1.5" />
		<path
			d="M2.5 17c.6-3.4 2.4-5 5-5s4.4 1.6 5 5"
			stroke="currentColor"
			stroke-width="1.5"
			stroke-linecap="round"
		/>
		<path
			d="M15 5v5.5M12.25 7.75h5.5"
			stroke="currentColor"
			stroke-width="1.5"
			stroke-linecap="round"
		/>
	</svg>
{:else if name === 'workflow'}
	<svg viewBox="0 0 20 20" fill="none" class={className} aria-hidden="true">
		<rect x="2" y="3" width="5" height="4" rx="1" stroke="currentColor" stroke-width="1.5" />
		<rect x="13" y="3" width="5" height="4" rx="1" stroke="currentColor" stroke-width="1.5" />
		<rect x="7.5" y="13" width="5" height="4" rx="1" stroke="currentColor" stroke-width="1.5" />
		<path
			d="M4.5 7v2.5a1 1 0 0 0 1 1H10m5.5-3.5v2.5a1 1 0 0 1-1 1H10m0 0V15"
			stroke="currentColor"
			stroke-width="1.5"
			stroke-linecap="round"
			stroke-linejoin="round"
		/>
	</svg>
{:else if name === 'wrench'}
	<svg viewBox="0 0 20 20" fill="none" class={className} aria-hidden="true">
		<path
			d="M12.7 3.3a4 4 0 0 0-5.2 4.9L3 12.7v2.6h2.6L10.1 11a4 4 0 0 0 4.9-5.2l-2.5 2.5-2-2 2.5-2.5Z"
			stroke="currentColor"
			stroke-width="1.5"
			stroke-linejoin="round"
		/>
	</svg>
{:else if name === 'flask'}
	<svg viewBox="0 0 20 20" fill="none" class={className} aria-hidden="true">
		<path
			d="M8 2.5h4M8.5 2.5v4.8L4.7 14a1.6 1.6 0 0 0 1.4 2.4h7.8a1.6 1.6 0 0 0 1.4-2.4L11.5 7.3V2.5"
			stroke="currentColor"
			stroke-width="1.5"
			stroke-linejoin="round"
			stroke-linecap="round"
		/>
		<path d="M6.3 11.5h7.4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" />
	</svg>
{:else if name === 'inbox-check'}
	<svg viewBox="0 0 20 20" fill="none" class={className} aria-hidden="true">
		<path
			d="M3 11.5 5 4h10l2 7.5M3 11.5v3a1.5 1.5 0 0 0 1.5 1.5h11A1.5 1.5 0 0 0 17 14.5v-3M3 11.5h4.2c.3 0 .6.2.7.5l.4 1c.1.3.4.5.7.5h2c.3 0 .6-.2.7-.5l.4-1c.1-.3.4-.5.7-.5H17"
			stroke="currentColor"
			stroke-width="1.4"
			stroke-linejoin="round"
			stroke-linecap="round"
		/>
	</svg>
{:else if name === 'info'}
	<svg viewBox="0 0 20 20" fill="none" class={className} aria-hidden="true">
		<circle cx="10" cy="10" r="7.2" stroke="currentColor" stroke-width="1.5" />
		<path d="M10 9v4.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" />
		<circle cx="10" cy="6.4" r=".9" fill="currentColor" />
	</svg>
{:else if name === 'book'}
	<svg viewBox="0 0 20 20" fill="none" class={className} aria-hidden="true">
		<path
			d="M3 4.5c1.6-.9 3.6-1 5.5-.3.7.3 1.3.7 1.5 1v10.6c-.2-.3-.8-.7-1.5-1-1.9-.7-3.9-.6-5.5.3V4.5Z"
			stroke="currentColor"
			stroke-width="1.5"
			stroke-linejoin="round"
		/>
		<path
			d="M17 4.5c-1.6-.9-3.6-1-5.5-.3-.7.3-1.3.7-1.5 1v10.6c.2-.3.8-.7 1.5-1 1.9-.7 3.9-.6 5.5.3V4.5Z"
			stroke="currentColor"
			stroke-width="1.5"
			stroke-linejoin="round"
		/>
	</svg>
{/if}
