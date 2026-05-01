import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import axios from "axios";
import MessageBubble from "../components/chat/MessageBubble";
import ChatComposer from "../components/chat/ChatComposer";
import AttachmentDropzone from "../components/attachments/AttachmentDropzone";
import { authApi, chatApi, extractApiError } from "../lib/api";
import { useAuthStore } from "../hooks/useAuthStore";
import { useChatStore } from "../hooks/useChatStore";

function buildLocalMessage({ role, content }) {
  return {
    id: crypto.randomUUID(),
    role,
    content,
    createdAt: new Date().toISOString(),
  };
}

export default function ChatPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [input, setInput] = useState("");
  const [error, setError] = useState("");

  const user = useAuthStore((state) => state.user);
  const clearAuth = useAuthStore((state) => state.clearAuth);

  const threads = useChatStore((state) => state.threads);
  const activeThreadId = useChatStore((state) => state.activeThreadId);
  const setThreads = useChatStore((state) => state.setThreads);
  const setActiveThread = useChatStore((state) => state.setActiveThread);
  const upsertThread = useChatStore((state) => state.upsertThread);
  const removeThread = useChatStore((state) => state.removeThread);
  const messages = useChatStore((state) => state.messages);
  const setMessages = useChatStore((state) => state.setMessages);
  const addMessage = useChatStore((state) => state.addMessage);
  const clearChatState = useChatStore((state) => state.clearChatState);

  const greeting = useMemo(() => {
    const firstName = user?.fullName?.split(" ")?.[0];
    return firstName || user?.email || "there";
  }, [user]);

  const threadsQuery = useQuery({
    queryKey: ["chat-threads", user?.email],
    queryFn: chatApi.getThreads,
    enabled: Boolean(user?.email),
    staleTime: 30_000,
  });

  const createThreadMutation = useMutation({
    mutationFn: chatApi.createThread,
    onSuccess: (thread) => {
      const normalized = {
        id: String(thread.id),
        title: thread.title,
        createdAt: thread.created_at,
        updatedAt: thread.updated_at,
      };
      upsertThread(normalized);
      setActiveThread(normalized.id);
      setMessages([]);
      setError("");
      queryClient.invalidateQueries({ queryKey: ["chat-threads", user?.email] });
    },
    onError: (mutationError) => {
      setError(extractApiError(mutationError, "Unable to create thread"));
    },
  });

  const renameThreadMutation = useMutation({
    mutationFn: chatApi.renameThread,
    onSuccess: (thread) => {
      upsertThread({
        id: String(thread.id),
        title: thread.title,
        createdAt: thread.created_at,
        updatedAt: thread.updated_at,
      });
      setError("");
    },
    onError: (mutationError) => {
      setError(extractApiError(mutationError, "Unable to rename thread"));
    },
  });

  const deleteThreadMutation = useMutation({
    mutationFn: chatApi.deleteThread,
    onSuccess: (_, variables) => {
      removeThread(variables.threadId);
      setMessages([]);
      queryClient.invalidateQueries({ queryKey: ["chat-threads", user?.email] });
      setError("");
    },
    onError: (mutationError) => {
      setError(extractApiError(mutationError, "Unable to delete thread"));
    },
  });

  const messagesQuery = useQuery({
    queryKey: ["thread-messages", activeThreadId],
    queryFn: () => chatApi.getThreadMessages({ threadId: activeThreadId }),
    enabled: Boolean(user?.email && activeThreadId),
    staleTime: 15_000,
  });

  useEffect(() => {
    if (!activeThreadId) {
      setMessages([]);
    }
  }, [activeThreadId, setMessages]);

  useEffect(() => {
    if (!threadsQuery.data) {
      return;
    }

    const normalizedThreads = threadsQuery.data.map((thread) => ({
      id: String(thread.id),
      title: thread.title,
      createdAt: thread.created_at,
      updatedAt: thread.updated_at,
    }));

    setThreads(normalizedThreads);

    if (normalizedThreads.length === 0 && !createThreadMutation.isPending) {
      createThreadMutation.mutate({});
      return;
    }

    if (!activeThreadId || !normalizedThreads.some((thread) => thread.id === activeThreadId)) {
      setActiveThread(normalizedThreads[0]?.id ?? null);
    }
  }, [
    activeThreadId,
    createThreadMutation.isPending,
    createThreadMutation.mutate,
    setActiveThread,
    setThreads,
    threadsQuery.data,
  ]);

  useEffect(() => {
    if (!messagesQuery.data) {
      return;
    }

    const normalizedMessages = messagesQuery.data.map((message) => ({
      id: String(message.id),
      threadId: message.thread_id ? String(message.thread_id) : null,
      role: message.role,
      content: message.content,
      createdAt: message.created_at,
    }));
    setMessages(normalizedMessages);
  }, [messagesQuery.data, setMessages]);

  useEffect(() => {
    if (!threadsQuery.isError) {
      return;
    }

    setMessages([]);

    if (axios.isAxiosError(threadsQuery.error) && threadsQuery.error.response?.status === 401) {
      clearAuth();
      navigate("/auth", { replace: true });
    }
  }, [threadsQuery.error, threadsQuery.isError, clearAuth, navigate, setMessages]);

  useEffect(() => {
    if (!messagesQuery.isError) {
      return;
    }

    if (axios.isAxiosError(messagesQuery.error) && messagesQuery.error.response?.status === 401) {
      clearAuth();
      navigate("/auth", { replace: true });
    }
  }, [messagesQuery.error, messagesQuery.isError, clearAuth, navigate]);

  const sendMutation = useMutation({
    mutationFn: chatApi.sendMessage,
    onSuccess: (assistantMessage) => {
      addMessage(
        buildLocalMessage({
          role: "assistant",
          content: assistantMessage.content,
        })
      );
      queryClient.invalidateQueries({ queryKey: ["chat-threads", user?.email] });
      setError("");
    },
    onError: (mutationError) => {
      const message = extractApiError(mutationError, "Unable to send message");
      setError(message);

      if (axios.isAxiosError(mutationError) && mutationError.response?.status === 401) {
        clearAuth();
        navigate("/auth", { replace: true });
      }
    },
  });

  const logoutMutation = useMutation({
    mutationFn: authApi.logout,
    onSettled: () => {
      clearAuth();
      clearChatState();
      navigate("/auth", { replace: true });
    },
  });

  const handleCreateThread = async () => {
    try {
      await createThreadMutation.mutateAsync({});
    } catch {
      // Error state is already handled in mutation onError.
    }
  };

  const handleRenameThread = (thread) => {
    const nextTitle = window.prompt("Rename thread", thread.title);
    if (!nextTitle || !nextTitle.trim()) {
      return;
    }

    renameThreadMutation.mutate({
      threadId: thread.id,
      title: nextTitle.trim(),
    });
  };

  const handleDeleteThread = (thread) => {
    const confirmed = window.confirm(`Delete thread \"${thread.title}\"?`);
    if (!confirmed) {
      return;
    }

    deleteThreadMutation.mutate({ threadId: thread.id });
  };

  const handleSend = async () => {
    const content = input.trim();
    if (!content || sendMutation.isPending || createThreadMutation.isPending) {
      return;
    }

    try {
      let destinationThreadId = activeThreadId;
      if (!destinationThreadId) {
        const createdThread = await createThreadMutation.mutateAsync({});
        destinationThreadId = String(createdThread.id);
      }

      addMessage(buildLocalMessage({ role: "user", content }));
      setInput("");
      setError("");
      sendMutation.mutate({ threadId: destinationThreadId, message: content });
    } catch {
      // Error state is already handled in mutation onError.
    }
  };

  return (
    <main className="chat-layout">
      <aside className="chat-sidebar">
        <div className="brand-lockup">
          <p className="eyebrow">amzur ai</p>
          <h2>Conversation Studio</h2>
          <p className="muted">A focused workspace for secure enterprise chat.</p>
        </div>

        <AttachmentDropzone />

        <section className="threads-panel">
          <div className="threads-panel__header">
            <p className="eyebrow">threads</p>
            <button
              className="secondary-btn"
              type="button"
              onClick={handleCreateThread}
              disabled={createThreadMutation.isPending}
            >
              {createThreadMutation.isPending ? "Creating..." : "New chat"}
            </button>
          </div>

          <div className="threads-list">
            {threads.map((thread) => (
              <div
                key={thread.id}
                className={`thread-item ${activeThreadId === thread.id ? "thread-item--active" : ""}`}
              >
                <button
                  className="thread-item__title"
                  type="button"
                  onClick={() => setActiveThread(thread.id)}
                >
                  {thread.title}
                </button>
                <div className="thread-item__actions">
                  <button type="button" onClick={() => handleRenameThread(thread)}>
                    Rename
                  </button>
                  <button type="button" onClick={() => handleDeleteThread(thread)}>
                    Delete
                  </button>
                </div>
              </div>
            ))}
          </div>
        </section>

        <button
          className="secondary-btn"
          type="button"
          onClick={() => logoutMutation.mutate()}
          disabled={logoutMutation.isPending}
        >
          {logoutMutation.isPending ? "Signing out..." : "Sign out"}
        </button>
      </aside>

      <section className="chat-main">
        <header className="chat-header">
          <h1>Hello, {greeting}</h1>
          <p>Ask a question and your assistant will respond with context-aware guidance.</p>
        </header>

        <div className="chat-stream">
          {messages.length === 0 ? (
            <div className="empty-state">
              <p>Start with something simple.</p>
              <p className="muted">Example: Summarize today's priorities in 5 bullet points.</p>
            </div>
          ) : (
            messages.map((message) => <MessageBubble key={message.id} message={message} />)
          )}
        </div>

        {error ? <p className="error-text chat-error">{error}</p> : null}

        <ChatComposer
          value={input}
          onChange={setInput}
          onSend={handleSend}
          sending={sendMutation.isPending}
        />
      </section>
    </main>
  );
}
