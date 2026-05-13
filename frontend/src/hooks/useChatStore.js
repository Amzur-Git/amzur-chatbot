import { create } from "zustand";

function getThreadTimestamp(thread) {
  const value = thread?.updatedAt || thread?.createdAt;
  const timestamp = value ? new Date(value).getTime() : 0;
  return Number.isFinite(timestamp) ? timestamp : 0;
}

function sortThreadsNewestFirst(threads) {
  return [...threads].sort((a, b) => getThreadTimestamp(b) - getThreadTimestamp(a));
}

export const useChatStore = create((set) => ({
  threads: [],
  activeThreadId: null,
  messages: [],

  setThreads: (threads) => set({ threads: sortThreadsNewestFirst(threads) }),

  setActiveThread: (threadId) => set({ activeThreadId: threadId }),

  upsertThread: (thread) =>
    set((state) => {
      const exists = state.threads.some((candidate) => candidate.id === thread.id);
      if (!exists) {
        return { threads: sortThreadsNewestFirst([thread, ...state.threads]) };
      }

      const updated = state.threads.map((candidate) =>
        candidate.id === thread.id ? { ...candidate, ...thread } : candidate
      );

      return {
        threads: sortThreadsNewestFirst(updated),
      };
    }),

  removeThread: (threadId) =>
    set((state) => {
      const nextThreads = state.threads.filter((thread) => thread.id !== threadId);
      const nextActiveThreadId =
        state.activeThreadId === threadId ? (nextThreads[0]?.id ?? null) : state.activeThreadId;

      return {
        threads: nextThreads,
        activeThreadId: nextActiveThreadId,
      };
    }),

  setMessages: (messages) => set({ messages }),

  addMessage: (message) =>
    set((state) => ({
      messages: [...state.messages, message],
    })),

  clearMessages: () => set({ messages: [] }),

  clearChatState: () =>
    set({
      threads: [],
      activeThreadId: null,
      messages: [],
    }),
}));
