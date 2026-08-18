// Owner-facing Google access boxes (#458). Mirrors
// server/src/rivulets/integrations/google_capabilities.py so Settings
// can render when the oauth-app payload is an older mock.

export interface GoogleCapability {
	id: string;
	group: string;
	group_label: string;
	label: string;
	write: boolean;
}

export const GOOGLE_CAPABILITIES: GoogleCapability[] = [
	{ id: 'gmail_read', group: 'gmail', group_label: 'Gmail', label: 'read', write: false },
	{
		id: 'gmail_write',
		group: 'gmail',
		group_label: 'Gmail',
		label: 'send and draft',
		write: true
	},
	{
		id: 'calendar_read',
		group: 'calendar',
		group_label: 'Calendar',
		label: 'read',
		write: false
	},
	{
		id: 'calendar_write',
		group: 'calendar',
		group_label: 'Calendar',
		label: 'create and update',
		write: true
	},
	{ id: 'drive_read', group: 'drive', group_label: 'Drive', label: 'read', write: false },
	{ id: 'drive_write', group: 'drive', group_label: 'Drive', label: 'write', write: true },
	{ id: 'docs_read', group: 'docs', group_label: 'Docs', label: 'read', write: false },
	{ id: 'docs_write', group: 'docs', group_label: 'Docs', label: 'append', write: true },
	{ id: 'sheets_read', group: 'sheets', group_label: 'Sheets', label: 'read', write: false },
	{ id: 'sheets_write', group: 'sheets', group_label: 'Sheets', label: 'update', write: true },
	{
		id: 'contacts_read',
		group: 'contacts',
		group_label: 'Contacts',
		label: 'search',
		write: false
	},
	{ id: 'tasks_read', group: 'tasks', group_label: 'Tasks', label: 'list', write: false },
	{ id: 'tasks_write', group: 'tasks', group_label: 'Tasks', label: 'add', write: true },
	{ id: 'meet_write', group: 'meet', group_label: 'Meet', label: 'create links', write: true }
];

export const GOOGLE_DEFAULT_CAPABILITIES: string[] = GOOGLE_CAPABILITIES.filter(
	(cap) => !cap.write
).map((cap) => cap.id);

export function googleCapabilityName(cap: GoogleCapability): string {
	return `${cap.group_label} — ${cap.label}`;
}

export function groupGoogleCapabilities(
	catalog: GoogleCapability[]
): { group: string; group_label: string; items: GoogleCapability[] }[] {
	const groups: { group: string; group_label: string; items: GoogleCapability[] }[] = [];
	for (const cap of catalog) {
		const last = groups.at(-1);
		if (last && last.group === cap.group) {
			last.items.push(cap);
		} else {
			groups.push({ group: cap.group, group_label: cap.group_label, items: [cap] });
		}
	}
	return groups;
}

export function formatGoogleCapabilities(
	ids: string[] | undefined,
	catalog: GoogleCapability[]
): string {
	if (!ids?.length) return '';
	const parts: string[] = [];
	for (const group of groupGoogleCapabilities(catalog)) {
		const granted = group.items.filter((cap) => ids.includes(cap.id));
		if (!granted.length) continue;
		parts.push(`${group.group_label} ${granted.map((cap) => cap.label).join(', ')}`);
	}
	return parts.join(' · ');
}

export function toggleGoogleCapability(
	selected: string[],
	catalog: GoogleCapability[],
	id: string,
	on: boolean
): string[] {
	const cap = catalog.find((item) => item.id === id);
	if (!cap) return selected;
	const have = new Set(selected);
	if (on) {
		have.add(id);
		if (cap.write) {
			const readId = id.replace(/_write$/, '_read');
			if (catalog.some((item) => item.id === readId)) have.add(readId);
		}
	} else if (cap.write) {
		have.delete(id);
	} else {
		const writeId = id.replace(/_read$/, '_write');
		if (have.has(writeId)) return selected;
		have.delete(id);
	}
	return catalog.filter((item) => have.has(item.id)).map((item) => item.id);
}
