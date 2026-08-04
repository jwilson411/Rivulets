// Thread & message resource client (FR-5, api-design.md#threads--messages).

import { api } from './client';
import { auth } from './auth.svelte';

export interface Thread {
	id: string;
	channel_id: string;
	title: string | null;
	status: 'active' | 'paused' | 'closed';
	created_by: string;
	created_at: string;
}

export type SenderType = 'human' | 'agent' | 'system';

export interface Message {
	id: string;
	thread_id: string;
	sender_type: SenderType;
	sender_id: string | null;
	sender_name: string;
	content: string;
	content_type: string;
	created_at: string;
}

export const threads = {
	listForChannel: (channelId: string) =>
		api.get<Thread[]>(`/channels/${channelId}/threads`, auth.token ?? undefined),
	create: (channelId: string, content: string) =>
		api.post<Thread>(`/channels/${channelId}/threads`, { content }, auth.token ?? undefined),
	get: (id: string) => api.get<Thread>(`/threads/${id}`, auth.token ?? undefined),
	listMessages: (id: string) =>
		api.get<Message[]>(`/threads/${id}/messages`, auth.token ?? undefined),
	postMessage: (id: string, content: string) =>
		api.post<Message>(`/threads/${id}/messages`, { content }, auth.token ?? undefined),
	resume: (id: string) => api.post<Thread>(`/threads/${id}/resume`, {}, auth.token ?? undefined)
};
