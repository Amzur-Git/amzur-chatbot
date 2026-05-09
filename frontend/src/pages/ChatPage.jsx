import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import axios from "axios";
import MessageBubble from "../components/chat/MessageBubble";
import ChatComposer from "../components/chat/ChatComposer";
import { attachmentsApi, authApi, chatApi, extractApiError } from "../lib/api";
import { useAuthStore } from "../hooks/useAuthStore";
import { useChatStore } from "../hooks/useChatStore";

function buildLocalMessage({ role, content }) {
  return {
    id: crypto.randomUUID(),
    role,
    content,
    createdAt: new Date().toISOString(),
    attachments: [],
  };
}

function buildLocalAttachment(file) {
  return {
    client_id: crypto.randomUUID(),
    id: null,
    file_name: file.name,
    file_type: "pending",
    file_size: file.size,
    created_at: new Date().toISOString(),
    metadata: {},
    uploading: true,
    progress: 0,
  };
}

export default function ChatPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [input, setInput] = useState("");
  const [formulaText, setFormulaText] = useState("");
  const [pendingAttachments, setPendingAttachments] = useState([]);
  const [imageOptions, setImageOptions] = useState({
    enabled: false,
    numImages: 1,
    aspectRatio: "1:1",
    negativePrompt: "",
    enhancePrompt: true,
  });
  const [imageStatus, setImageStatus] = useState("idle");
  const [imageStatusMessage, setImageStatusMessage] = useState("");
  const [error, setError] = useState("");
  const [lastSendAttempt, setLastSendAttempt] = useState(null);
  const [failedSend, setFailedSend] = useState(null);

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

  const uploading = pendingAttachments.some((item) => item.uploading);

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

  const uploadAttachmentMutation = useMutation({
    mutationFn: attachmentsApi.upload,
    onError: (mutationError) => {
      setError(extractApiError(mutationError, "Unable to upload attachment"));
    },
  });

  const deleteAttachmentMutation = useMutation({
    mutationFn: attachmentsApi.delete,
  });

  useEffect(() => {
    // Reset message pane immediately on thread switch to avoid showing stale content.
    setMessages([]);
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

    if (!activeThreadId || !normalizedThreads.some((thread) => thread.id === activeThreadId)) {
      setActiveThread(normalizedThreads[0]?.id ?? null);
    }
  }, [
    activeThreadId,
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
      attachments: (message.attachments || []).map((attachment) => ({
        id: String(attachment.id),
        file_type: attachment.file_type,
        file_name: attachment.file_name,
        file_size: attachment.file_size,
        created_at: attachment.created_at,
        metadata: attachment.metadata || {},
      })),
    }));
    setMessages(normalizedMessages);
  }, [messagesQuery.data, setMessages]);

  useEffect(() => {
    if (!threadsQuery.isError) {
      return;
    }

    setMessages([]);
    setError(extractApiError(threadsQuery.error, "Unable to load chat threads"));

    if (axios.isAxiosError(threadsQuery.error) && threadsQuery.error.response?.status === 401) {
      clearAuth();
      navigate("/auth", { replace: true });
    }
  }, [threadsQuery.error, threadsQuery.isError, clearAuth, navigate, setMessages, setError]);

  useEffect(() => {
    if (!messagesQuery.isError) {
      return;
    }

    setMessages([]);
    setError(extractApiError(messagesQuery.error, "Unable to load this conversation"));

    if (axios.isAxiosError(messagesQuery.error) && messagesQuery.error.response?.status === 401) {
      clearAuth();
      navigate("/auth", { replace: true });
    }
  }, [messagesQuery.error, messagesQuery.isError, clearAuth, navigate, setMessages, setError]);

  const sendMutation = useMutation({
    mutationFn: chatApi.sendMessage,
    onSuccess: (assistantMessage) => {
      const assistantAttachments = (assistantMessage.attachments || []).map((attachment) => ({
        id: String(attachment.id),
        file_type: attachment.file_type,
        file_name: attachment.file_name,
        file_size: attachment.file_size,
        created_at: attachment.created_at,
        metadata: attachment.metadata || {},
      }));

      addMessage(
        {
          ...buildLocalMessage({
          role: "assistant",
          content: assistantMessage.content,
          }),
          attachments: assistantAttachments,
        }
      );

      const generatedImageCount = assistantAttachments.filter(
        (attachment) => attachment.file_type === "image" && attachment.metadata?.generated
      ).length;
      if (generatedImageCount > 0) {
        setImageStatus("success");
        setImageStatusMessage(`Generated ${generatedImageCount} image(s)`);
      } else {
        setImageStatus("idle");
        setImageStatusMessage("");
      }

      queryClient.invalidateQueries({ queryKey: ["chat-threads", user?.email] });
      queryClient.invalidateQueries({ queryKey: ["thread-messages", activeThreadId] });
      setPendingAttachments([]);
      setFormulaText("");
      setLastSendAttempt(null);
      setFailedSend(null);
      setError("");
    },
    onError: (mutationError) => {
      const message = extractApiError(mutationError, "Unable to send message");
      setError(message);
      setImageStatus("error");
      setImageStatusMessage(message);

      if (lastSendAttempt) {
        setFailedSend({
          messageId: lastSendAttempt.localMessageId,
          payload: lastSendAttempt.payload,
        });
      }

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

  const uploadFilesToThread = async (threadId, files) => {
    const localItems = files.map((file) => buildLocalAttachment(file));
    setPendingAttachments((current) => [...current, ...localItems]);

    await Promise.all(
      files.map(async (file, index) => {
        const local = localItems[index];

        try {
          const uploaded = await uploadAttachmentMutation.mutateAsync({
            threadId,
            file,
            onUploadProgress: (event) => {
              const nextProgress = event.total
                ? Math.round((event.loaded / event.total) * 100)
                : 0;
              setPendingAttachments((current) =>
                current.map((item) =>
                  item.client_id === local.client_id
                    ? { ...item, progress: nextProgress }
                    : item
                )
              );
            },
          });

          setPendingAttachments((current) =>
            current.map((item) =>
              item.client_id === local.client_id
                ? {
                    ...item,
                    id: String(uploaded.id),
                    file_type: uploaded.file_type,
                    file_name: uploaded.file_name,
                    file_size: uploaded.file_size,
                    created_at: uploaded.created_at,
                    metadata: uploaded.metadata || {},
                    uploading: false,
                    progress: 100,
                  }
                : item
            )
          );
        } catch {
          setPendingAttachments((current) =>
            current.filter((item) => item.client_id !== local.client_id)
          );
        }
      })
    );
  };

  const handlePickFiles = async (files) => {
    try {
      let destinationThreadId = activeThreadId;
      if (!destinationThreadId) {
        const createdThread = await createThreadMutation.mutateAsync({});
        destinationThreadId = String(createdThread.id);
      }

      await uploadFilesToThread(destinationThreadId, files);
    } catch {
      // handled by mutation onError
    }
  };

  const handleRemoveAttachment = async (attachment) => {
    if (!attachment.id) {
      setPendingAttachments((current) =>
        current.filter((item) => item.client_id !== attachment.client_id)
      );
      return;
    }

    try {
      await deleteAttachmentMutation.mutateAsync({ attachmentId: attachment.id });
      setPendingAttachments((current) =>
        current.filter((item) => item.client_id !== attachment.client_id)
      );
    } catch (mutationError) {
      setError(extractApiError(mutationError, "Unable to remove attachment"));
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
    const confirmed = window.confirm(`Delete thread "${thread.title}"?`);
    if (!confirmed) {
      return;
    }

    deleteThreadMutation.mutate({ threadId: thread.id });
  };

  const handleSend = async () => {
    const content = input.trim();
    const canSend = content || formulaText.trim() || pendingAttachments.length > 0;
    if (!canSend || sendMutation.isPending || createThreadMutation.isPending || uploading) {
      return;
    }

    try {
      let destinationThreadId = activeThreadId;
      if (!destinationThreadId) {
        const createdThread = await createThreadMutation.mutateAsync({});
        destinationThreadId = String(createdThread.id);
      }

      const readyAttachments = pendingAttachments.filter((item) => item.id && !item.uploading);
      const messageAttachments = readyAttachments.map((attachment) => ({
        id: String(attachment.id),
        file_type: attachment.file_type,
        file_name: attachment.file_name,
        file_size: attachment.file_size,
        created_at: attachment.created_at,
        metadata: attachment.metadata || {},
      }));

      const localUserMessage = {
        ...buildLocalMessage({ role: "user", content: content || "[attachment/formula message]" }),
        attachments: messageAttachments,
      };
      addMessage(localUserMessage);

      const payload = {
        threadId: destinationThreadId,
        message: content || "Please process the attached content.",
        attachmentIds: readyAttachments.map((item) => item.id),
        formulaText: formulaText.trim() || null,
        numImages: imageOptions.enabled ? imageOptions.numImages : null,
        aspectRatio: imageOptions.enabled ? imageOptions.aspectRatio : null,
        negativePrompt: imageOptions.enabled ? imageOptions.negativePrompt.trim() || null : null,
        enhancePrompt: imageOptions.enabled ? imageOptions.enhancePrompt : true,
      };

      if (imageOptions.enabled) {
        setImageStatus("generating");
        setImageStatusMessage("Generating image(s)...");
      } else {
        setImageStatus("idle");
        setImageStatusMessage("");
      }

      setInput("");
      setError("");
      setLastSendAttempt({
        localMessageId: localUserMessage.id,
        payload,
      });
      setFailedSend(null);
      sendMutation.mutate(payload);
    } catch {
      // Error state is already handled in mutation onError.
    }
  };

  const handleRetryFailedPrompt = () => {
    if (!failedSend || sendMutation.isPending || createThreadMutation.isPending || uploading) {
      return;
    }

    if (failedSend.payload?.numImages) {
      setImageStatus("generating");
      setImageStatusMessage("Generating image(s)...");
    } else {
      setImageStatus("idle");
      setImageStatusMessage("");
    }

    setError("");
    setLastSendAttempt({
      localMessageId: failedSend.messageId,
      payload: failedSend.payload,
    });
    sendMutation.mutate(failedSend.payload);
  };

  return (
    <main className="chat-layout">
      <aside className="chat-sidebar">
        <div className="brand-lockup">
          <p className="eyebrow">amzur ai</p>
          <h2>Conversation Studio</h2>
          <p className="muted">A focused workspace for secure enterprise chat.</p>
        </div>

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
                  onClick={() => {
                    setActiveThread(thread.id);
                    setMessages([]);
                    setPendingAttachments([]);
                    setFormulaText("");
                    setError("");
                  }}
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
          {activeThreadId && messagesQuery.isFetching ? (
            <div className="empty-state">
              <p>Loading conversation...</p>
            </div>
          ) : messages.length === 0 ? (
            <div className="empty-state">
              <p>Start with something simple.</p>
              <p className="muted">Example: Summarize today's priorities in 5 bullet points.</p>
            </div>
          ) : (
            messages.map((message) => (
              <MessageBubble
                key={message.id}
                message={message}
                showRetry={failedSend?.messageId === message.id}
                onRetry={handleRetryFailedPrompt}
                retrying={sendMutation.isPending}
              />
            ))
          )}
        </div>

        {imageStatus !== "idle" ? (
          <p className={`chat-image-status chat-image-status--${imageStatus}`}>{imageStatusMessage}</p>
        ) : null}

        {error ? <p className="error-text chat-error">{error}</p> : null}

        <ChatComposer
          value={input}
          onChange={setInput}
          onSend={handleSend}
          sending={sendMutation.isPending}
          attachments={pendingAttachments}
          onPickFiles={handlePickFiles}
          onRemoveAttachment={handleRemoveAttachment}
          uploading={uploading}
          formulaText={formulaText}
          onFormulaTextChange={setFormulaText}
          imageOptions={imageOptions}
          onImageOptionsChange={setImageOptions}
        />
      </section>
    </main>
  );
}
