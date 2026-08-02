// Ingestion capability adapter — observations, document ingest jobs.
//
// Route ownership is the generated table in ../contracts/capabilities.ts;
// functions here call only routes that table assigns to 'ingestion'.

import type {
  ObservationSummary,
  ObservationDetail,
  ObservationEvalResponse,
  ObservationApplyResponse,
} from '../types';
import { json } from './http';
import { getAuthHeaders } from '../auth';

// ── Observation Review Pipeline ──
export function fetchObservations(status?: string, limit = 50): Promise<ObservationSummary[]> {
  const params = new URLSearchParams();
  if (status) params.set('status', status);
  params.set('limit', String(limit));
  return json<ObservationSummary[]>(`/ingest/observations?${params}`);
}

export function fetchObservationDetail(obsId: number): Promise<ObservationDetail> {
  return json<ObservationDetail>(`/ingest/observations/${obsId}`);
}

export function evaluateObservation(obsId: number, model: string): Promise<ObservationEvalResponse> {
  return json<ObservationEvalResponse>(`/ingest/observations/${obsId}/evaluate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ model }),
  });
}

export function evaluateObservationBatch(ids: number[], model: string): Promise<ObservationEvalResponse[]> {
  return json<ObservationEvalResponse[]>('/ingest/observations/evaluate-batch', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ observation_ids: ids, model }),
  });
}

export function applyObservation(obsId: number, updateIndices: number[], newNeuronIndices: number[]): Promise<ObservationApplyResponse> {
  return json<ObservationApplyResponse>(`/ingest/observations/${obsId}/apply`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ update_indices: updateIndices, new_neuron_indices: newNeuronIndices }),
  });
}

export function approveObservation(obsId: number): Promise<{ observation_id: number; neuron_id: number }> {
  return json<{ observation_id: number; neuron_id: number }>(`/ingest/observations/${obsId}/approve`, { method: 'POST' });
}

export function rejectObservation(obsId: number): Promise<{ observation_id: number; status: string }> {
  return json<{ observation_id: number; status: string }>(`/ingest/observations/${obsId}/reject`, { method: 'POST' });
}

// ── Document Ingest ──
export interface DocumentIngestJob {
  id: string;
  status: string;
  step: string;
  filename: string;
  file_format: string;
  file_size_bytes: number;
  total_pages: number | null;
  title: string;
  source_type: string;
  authority_level: string;
  citation: string;
  source_url: string | null;
  department: string | null;
  role_key: string | null;
  total_sections: number;
  current_section: number;
  proposal_ids: number[];
  cost_usd: number;
  input_tokens: number;
  output_tokens: number;
  model: string;
  duplicates_flagged: number;
  errors: string[];
  created_at: string | null;
  updated_at: string | null;
}

export interface DocumentStructure {
  title: string;
  total_pages: number | null;
  sections: Array<{
    id: string;
    title: string;
    level: number;
    char_start: number;
    char_end: number;
    page_start: number | null;
    page_end: number | null;
    parent_section_id: string | null;
  }>;
}

export async function uploadDocument(
  file: File,
  metadata: {
    title?: string;
    source_type?: string;
    authority_level?: string;
    citation?: string;
    source_url?: string;
    department?: string;
    role_key?: string;
    model?: string;
  },
): Promise<DocumentIngestJob> {
  const authHeaders = getAuthHeaders();

  const form = new FormData();
  form.append('file', file);
  if (metadata.title) form.append('title', metadata.title);
  if (metadata.source_type) form.append('source_type', metadata.source_type);
  if (metadata.authority_level) form.append('authority_level', metadata.authority_level);
  if (metadata.citation) form.append('citation', metadata.citation);
  if (metadata.source_url) form.append('source_url', metadata.source_url);
  if (metadata.department) form.append('department', metadata.department);
  if (metadata.role_key) form.append('role_key', metadata.role_key);
  if (metadata.model) form.append('model', metadata.model);

  const res = await fetch('/admin/documents/upload', {
    method: 'POST',
    headers: authHeaders,
    body: form,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new Error(body?.detail || `${res.status} ${res.statusText}`);
  }
  return res.json();
}

export function fetchDocumentJobs(status?: string): Promise<DocumentIngestJob[]> {
  const params = status ? `?status=${status}` : '';
  return json<DocumentIngestJob[]>(`/admin/documents/${params}`);
}

export function fetchDocumentJobStatus(jobId: string): Promise<DocumentIngestJob> {
  return json<DocumentIngestJob>(`/admin/documents/${jobId}`);
}

export function fetchDocumentStructure(jobId: string): Promise<DocumentStructure> {
  return json<DocumentStructure>(`/admin/documents/${jobId}/structure`);
}

export function cancelDocumentJob(jobId: string): Promise<DocumentIngestJob> {
  return json<DocumentIngestJob>(`/admin/documents/${jobId}/cancel`, { method: 'POST' });
}
