// Rivulet & message resource client (FR-5, api-design.md#rivulets--messages).

import { api } from './client';
import { auth } from './auth.svelte';

export interface Rivulet {
	id: string;
	channel_id: string;
	title: string | null;
	status: 'active' | 'paused' | 'closed';
	created_by: string;
	created_at: string;
}

export type SenderType = 'human' | 'agent' | 'system';

export interface Attachment {
	file_id: string;
	filename: string;
	mime_type: string;
	size_bytes: number;
}

export interface Message {
	id: string;
	rivulet_id: string;
	sender_type: SenderType;
	sender_id: string | null;
	sender_name: string;
	content: string;
	content_type: string;
	created_at: string;
	attachments: Attachment[];
	// Set only on replies from an "auto" mode agent (#23) -- which
	// concrete model actually answered this message, and which tier
	// (cheap/capable) it was classified into.
	model_used: string | null;
	tier: string | null;
	// Issue #10: which node actually ran the agent that produced this
	// message -- null for human messages, or when the sync engine wasn't
	// running at reply time.
	executed_node_id: string | null;
	// #103: set only when the agent's configured fallback chain served
	// this reply because its primary model's call failed with a
	// retryable-looking error -- the model that actually answered.
	served_model: string | null;
}

export const rivulets = {
	listForChannel: (channelId: string) =>
		api.get<Rivulet[]>(`/channels/${channelId}/rivulets`, auth.token ?? undefined),
	create: (channelId: string, content: string, fileIds: string[] = []) =>
		api.post<Rivulet>(
			`/channels/${channelId}/rivulets`,
			{ content, files: fileIds },
			auth.token ?? undefined
		),
	get: (id: string) => api.get<Rivulet>(`/rivulets/${id}`, auth.token ?? undefined),
	listMessages: (id: string) =>
		api.get<Message[]>(`/rivulets/${id}/messages`, auth.token ?? undefined),
	postMessage: (id: string, content: string, fileIds: string[] = []) =>
		api.post<Message>(
			`/rivulets/${id}/messages`,
			{ content, files: fileIds },
			auth.token ?? undefined
		),
	resume: (id: string) => api.post<Rivulet>(`/rivulets/${id}/resume`, {}, auth.token ?? undefined)
};
