import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
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
  const [input, setInput] = useState("");
  const [error, setError] = useState("");

  const user = useAuthStore((state) => state.user);
  const clearAuth = useAuthStore((state) => state.clearAuth);

  const messages = useChatStore((state) => state.messages);
  const setMessages = useChatStore((state) => state.setMessages);
  const addMessage = useChatStore((state) => state.addMessage);
  const clearMessages = useChatStore((state) => state.clearMessages);

  const greeting = useMemo(() => {
    const firstName = user?.fullName?.split(" ")?.[0];
    return firstName || user?.email || "there";
  }, [user]);

  const historyQuery = useQuery({
    queryKey: ["chat-history", user?.email],
    queryFn: chatApi.getHistory,
    enabled: Boolean(user?.email),
    staleTime: 30_000,
  });

  useEffect(() => {
    if (!historyQuery.data) {
      return;
    }

    const normalized = historyQuery.data.map((message) => ({
      id: String(message.id),
      role: message.role,
      content: message.content,
      createdAt: message.created_at,
    }));
    setMessages(normalized);
  }, [historyQuery.data, setMessages]);

  useEffect(() => {
    if (!historyQuery.isError) {
      return;
    }

    setMessages([]);

    if (axios.isAxiosError(historyQuery.error) && historyQuery.error.response?.status === 401) {
      clearAuth();
      navigate("/auth", { replace: true });
    }
  }, [historyQuery.error, historyQuery.isError, clearAuth, navigate, setMessages]);

  const sendMutation = useMutation({
    mutationFn: chatApi.sendMessage,
    onSuccess: (assistantMessage) => {
      addMessage(
        buildLocalMessage({
          role: "assistant",
          content: assistantMessage.content,
        })
      );
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
      clearMessages();
      navigate("/auth", { replace: true });
    },
  });

  const handleSend = () => {
    const content = input.trim();
    if (!content || sendMutation.isPending) {
      return;
    }

    addMessage(buildLocalMessage({ role: "user", content }));
    setInput("");
    setError("");
    sendMutation.mutate({ message: content });
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
