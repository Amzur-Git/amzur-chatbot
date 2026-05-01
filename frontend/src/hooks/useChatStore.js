import { create } from "zustand";

export const useChatStore = create((set) => ({
  threads: [],
  activeThreadId: null,
  messages: [],

  setThreads: (threads) => set({ threads }),

  setActiveThread: (threadId) => set({ activeThreadId: threadId }),

  upsertThread: (thread) =>
    set((state) => {
      const exists = state.threads.some((candidate) => candidate.id === thread.id);
      if (!exists) {
        return { threads: [thread, ...state.threads] };
      }

      return {
        threads: state.threads.map((candidate) =>
          candidate.id === thread.id ? { ...candidate, ...thread } : candidate
        ),
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
