<script lang="ts">
	// #351: owner-only routes (/providers, /sync, /invites) are nav-hidden
	// for invite-grant sessions but still directly routable. Rendering this
	// instead of the real page means a non-owner who lands on one sees a
	// deliberate empty state, not a stack of 403 load errors -- and the page
	// never fires the OwnerGrant-gated requests that could only fail.
	let { title }: { title: string } = $props();
</script>

<div class="mx-auto flex max-w-2xl flex-col gap-2 px-6 py-8">
	<h1 class="text-2xl font-semibold text-ink dark:text-ink-dark">{title}</h1>
	<p class="text-sm text-neutral-600 dark:text-neutral-400">
		This page is only available to the workspace owner — your session doesn't have access to it.
	</p>
</div>
