// Knowledge base resource client (#98, api-design.md-style CRUD +
// document ingestion). Document ingestion (`ingestDocument`) takes a
// `file_id` from `files.upload()` (files.ts) -- upload and ingest are two
// separate steps, matching how the App Server splits them.

import { api } from './client';
import { auth } from './auth.svelte';

export interface KnowledgeBase {
	id: string;
	name: string;
	description: string | null;
	scope_type: 'agent' | 'team';
	agent_id: string | null;
	team_id: string | null;
	document_count: number;
}

export interface KnowledgeBaseDocument {
	id: string;
	knowledge_base_id: string;
	file_id: string;
	filename: string;
	status: 'pending' | 'ingested' | 'failed';
	error_message: string | null;
	chunk_count: number;
}

export const knowledgeBases = {
	list: () => api.get<KnowledgeBase[]>('/knowledge-bases', auth.token ?? undefined),
	get: (id: string) => api.get<KnowledgeBase>(`/knowledge-bases/${id}`, auth.token ?? undefined),
	create: (body: {
		name: string;
		description?: string;
		scope_type: 'agent' | 'team';
		agent_id?: string;
		team_id?: string;
	}) => api.post<KnowledgeBase>('/knowledge-bases', body, auth.token ?? undefined),
	update: (id: string, patch: { name?: string; description?: string }) =>
		api.patch<KnowledgeBase>(`/knowledge-bases/${id}`, patch, auth.token ?? undefined),
	remove: (id: string) => api.delete<void>(`/knowledge-bases/${id}`, auth.token ?? undefined),

	listDocuments: (kbId: string) =>
		api.get<KnowledgeBaseDocument[]>(`/knowledge-bases/${kbId}/documents`, auth.token ?? undefined),
	ingestDocument: (kbId: string, fileId: string) =>
		api.post<KnowledgeBaseDocument>(
			`/knowledge-bases/${kbId}/documents`,
			{ file_id: fileId },
			auth.token ?? undefined
		),
	removeDocument: (kbId: string, documentId: string) =>
		api.delete<void>(`/knowledge-bases/${kbId}/documents/${documentId}`, auth.token ?? undefined)
};
