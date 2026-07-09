import type { NeuronScoreResponse } from './types';

/* Cross-window chat bus. The Home window's chat (HomePage) owns all chat
   state; the Chat History and Neuron Graph windows are separate React
   subtrees, so they talk to it through window events plus a last-value
   store (so a window opened late still gets the current graph). */

export const CHAT_STARTED_EVENT = 'corvus-chat-started';            // HomePage → App: open companion windows
export const CHAT_LOAD_SESSION_EVENT = 'corvus-chat-load-session';  // History window → HomePage
export const CHAT_NEW_EVENT = 'corvus-chat-new';                    // History window → HomePage
export const CHAT_GRAPH_EVENT = 'corvus-chat-graph';                // HomePage → Graph window
export const CHAT_SESSIONS_CHANGED_EVENT = 'corvus-chat-sessions-changed'; // HomePage → History window

export interface GraphPayload {
  queryId?: number;
  neuronScores: NeuronScoreResponse[] | null;
}

let lastGraph: GraphPayload = { neuronScores: null };

export function publishGraph(payload: GraphPayload): void {
  lastGraph = payload;
  window.dispatchEvent(new Event(CHAT_GRAPH_EVENT));
}

export function getLastGraph(): GraphPayload {
  return lastGraph;
}

export function announceChatStarted(): void {
  window.dispatchEvent(new Event(CHAT_STARTED_EVENT));
}

export function requestLoadSession(id: number): void {
  window.dispatchEvent(new CustomEvent(CHAT_LOAD_SESSION_EVENT, { detail: id }));
}

export function requestNewChat(): void {
  window.dispatchEvent(new Event(CHAT_NEW_EVENT));
}

export function announceSessionsChanged(): void {
  window.dispatchEvent(new Event(CHAT_SESSIONS_CHANGED_EVENT));
}
