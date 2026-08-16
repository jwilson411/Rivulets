<script lang="ts">
	import { initials } from '$lib/ink';

	// Agent / human identity disc: a rounded-xl plate with an initial,
	// colored by ink (03-design-direction.md's iconography rules — never a
	// photo, never color alone: callers show the name next to it).
	let {
		name,
		colorClass,
		size = 32,
		class: className = ''
	}: {
		name: string;
		colorClass: string;
		size?: 20 | 24 | 28 | 32 | 36 | 40;
		class?: string;
	} = $props();

	// Radius and type scale with the disc, matching the mockups.
	const bySize: Record<number, string> = {
		20: 'h-5 w-5 rounded-[7px] text-[10px]',
		24: 'h-6 w-6 rounded-[8px] text-[11px]',
		28: 'h-7 w-7 rounded-[10px] text-[13px]',
		32: 'h-8 w-8 rounded-[12px] text-sm',
		36: 'h-9 w-9 rounded-[12px] text-sm',
		40: 'h-10 w-10 rounded-[14px] text-base'
	};
</script>

<span
	title={name}
	class="inline-flex flex-none items-center justify-center font-semibold {bySize[
		size
	]} {colorClass} {className}"
>
	{initials(name)}
</span>
