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
{/if}
