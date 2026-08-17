<script lang="ts">
	// #351: owner-only routes (/providers, /sync, /invites) are nav-hidden
	// for invite-grant sessions but still directly routable. Rendering this
	// instead of the real page means a non-owner who lands on one sees a
	// deliberate empty state, not a stack of 403 load errors -- and the page
	// never fires the OwnerGrant-gated requests that could only fail.
	let { title }: { title: string } = $props();
</script>

<div class="mx-auto max-w-[720px] px-4 pt-8 md:px-10">
	<h1 class="mb-6 font-display text-[28px] font-semibold text-ink dark:text-ink-dark">{title}</h1>
	<p class="py-6 text-center text-base text-muted dark:text-muted-dark">
		This is only available to the workspace owner.
	</p>
</div>
