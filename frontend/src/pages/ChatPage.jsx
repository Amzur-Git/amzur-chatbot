import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import axios from "axios";
import { ChevronDown } from "lucide-react";
import MessageBubble from "../components/chat/MessageBubble";
import ChatComposer from "../components/chat/ChatComposer";
import { attachmentsApi, authApi, chatApi, extractApiError, sheetsApi, workflowsApi } from "../lib/api";
import { useAuthStore } from "../hooks/useAuthStore";
import { useChatStore } from "../hooks/useChatStore";

function hasLikelyImageIntent(text) {
  const value = String(text || "").trim();
  if (!value) {
    return false;
  }

  if (/^\s*\/imagine\b/i.test(value)) {
    return true;
  }

  return /(generate|create|make|draw|design|render)\b.{0,24}\b(image|picture|photo|art|illustration)|\b(image|picture|photo|illustration)\b.{0,18}\b(of|for|showing|with)/i.test(
    value
  );
}

function inferFormulaText(text) {
  const value = String(text || "").trim();
  if (!value) {
    return null;
  }

  const hasLatexMarker =
    /\$[^$]+\$/.test(value) ||
    /\\(frac|sqrt|sum|int|alpha|beta|gamma|theta|pi|sin|cos|tan|log|ln)\b/i.test(value) ||
    /[\^_{}]/.test(value);

  const hasPlainEquation =
    /(?:\b[\w)\]]+\s*(?:=|≈|≃|<=|>=|<|>)\s*[[\w(]+\b)/i.test(value) ||
    /(?:\b(?:\d+(?:\.\d+)?|[a-z])\s*[+\-*/^×÷]\s*(?:\d+(?:\.\d+)?|[a-z])(?:\s*[+\-*/^×÷]\s*(?:\d+(?:\.\d+)?|[a-z]))*\b)/i.test(
      value
    );

  return hasLatexMarker || hasPlainEquation ? value : null;
}

const SHEETS_QUERY_RATE_LIMIT_MS = 1_500;

function isValidGoogleSheetsUrl(value) {
  try {
    const url = new URL(String(value || "").trim());
    if (!/docs\.google\.com$/i.test(url.hostname)) {
      return false;
    }
    return /^\/spreadsheets\/d\/[a-zA-Z0-9-_]+/i.test(url.pathname);
  } catch {
    return false;
  }
}

function sanitizeSheetsQuestion(value) {
  const normalized = String(value || "")
    .split("")
    .map((char) => {
      const code = char.charCodeAt(0);
      return code <= 31 || code === 127 ? " " : char;
    })
    .join("")
    .trim();
  if (!normalized) {
    return "";
  }

  // Keep requests reasonably bounded for latency and token safety.
  return normalized.slice(0, 1_500);
}

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

function toUiAttachment(attachment) {
  return {
    id: String(attachment.id),
    file_type: attachment.file_type,
    file_name: attachment.file_name,
    file_size: attachment.file_size,
    created_at: attachment.created_at,
    metadata: attachment.metadata || {},
  };
}

function toUiMessage(message) {
  return {
    id: String(message.id),
    threadId: message.thread_id ? String(message.thread_id) : null,
    parentMessageId: message.parent_message_id ? String(message.parent_message_id) : null,
    role: message.role,
    content: message.content,
    createdAt: message.created_at,
    attachments: (message.attachments || []).map(toUiAttachment),
  };
}

function isPersistedUuid(value) {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(
    String(value || "")
  );
}

function isLikelyPersistedMessage(message, activeThreadId) {
  const messageId = String(message?.id || "").trim();
  if (!messageId || messageId.startsWith("temp-")) {
    return false;
  }

  const threadId = String(message?.threadId || "").trim();
  return Boolean(activeThreadId) && threadId === String(activeThreadId);
}

const IMAGE_OPTIONS_STORAGE_KEY = "amzur-chatbot:image-options";
const LAST_ACTIVE_THREAD_STORAGE_PREFIX = "amzur-chatbot:last-active-thread:";
const DEFAULT_IMAGE_OPTIONS = {
  numImages: 1,
  aspectRatio: "1:1",
  negativePrompt: "",
  enhancePrompt: true,
};
const ALLOWED_ASPECT_RATIOS = new Set(["1:1", "3:4", "4:3", "16:9", "9:16"]);

function normalizeImageOptions(value) {
  const candidate = value && typeof value === "object" ? value : {};
  const numImages = Number(candidate.numImages);
  const normalizedNumImages = Number.isFinite(numImages)
    ? Math.min(4, Math.max(1, Math.round(numImages)))
    : DEFAULT_IMAGE_OPTIONS.numImages;

  const aspectRatio = ALLOWED_ASPECT_RATIOS.has(candidate.aspectRatio)
    ? candidate.aspectRatio
    : DEFAULT_IMAGE_OPTIONS.aspectRatio;

  return {
    numImages: normalizedNumImages,
    aspectRatio,
    negativePrompt:
      typeof candidate.negativePrompt === "string"
        ? candidate.negativePrompt
        : DEFAULT_IMAGE_OPTIONS.negativePrompt,
    enhancePrompt:
      typeof candidate.enhancePrompt === "boolean"
        ? candidate.enhancePrompt
        : DEFAULT_IMAGE_OPTIONS.enhancePrompt,
  };
}

function getLastActiveThreadStorageKey(userEmail) {
  const normalized = String(userEmail || "").trim().toLowerCase();
  return `${LAST_ACTIVE_THREAD_STORAGE_PREFIX}${normalized || "anonymous"}`;
}

export default function ChatPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [input, setInput] = useState("");
  const [pendingAttachments, setPendingAttachments] = useState([]);
  const [imageOptions, setImageOptions] = useState(() => {
    if (typeof window === "undefined") {
      return DEFAULT_IMAGE_OPTIONS;
    }

    try {
      const raw = window.localStorage.getItem(IMAGE_OPTIONS_STORAGE_KEY);
      if (!raw) {
        return DEFAULT_IMAGE_OPTIONS;
      }

      return normalizeImageOptions(JSON.parse(raw));
    } catch {
      return DEFAULT_IMAGE_OPTIONS;
    }
  });
  const [imageStatus, setImageStatus] = useState("idle");
  const [imageStatusMessage, setImageStatusMessage] = useState("");
  const [dbQueryMode, setDbQueryMode] = useState(false);
  const [sheetsQueryMode, setSheetsQueryMode] = useState(false);
  const [sheetsFileAttachment, setSheetsFileAttachment] = useState(null);
  const [sheetsUrlInput, setSheetsUrlInput] = useState("");
  const [loadedSheetsUrl, setLoadedSheetsUrl] = useState("");
  const [sheetsPreview, setSheetsPreview] = useState(null);
  const [loadingSheetsPreview, setLoadingSheetsPreview] = useState(false);
  const [sheetsQuerying, setSheetsQuerying] = useState(false);
  const [lastSheetsQueryAt, setLastSheetsQueryAt] = useState(0);
  const [error, setError] = useState("");
  const [lastSendAttempt, setLastSendAttempt] = useState(null);
  const [failedSend, setFailedSend] = useState(null);
  const [editingMessageId, setEditingMessageId] = useState(null);
  const [editingDraft, setEditingDraft] = useState("");
  const [showJumpToLatest, setShowJumpToLatest] = useState(false);
  const chatStreamRef = useRef(null);
  const chatBottomRef = useRef(null);
  const shouldAutoScrollRef = useRef(true);
  const previousThreadRef = useRef(null);
  const previousMessageCountRef = useRef(0);
  const previousLastMessageIdRef = useRef(null);
  const previousLastMessageContentRef = useRef("");
  const pendingInstantScrollRef = useRef(true);
  const createThreadGuardRef = useRef(false);
  const optimisticNewThreadIdRef = useRef(null);
  const optimisticPreviousThreadIdRef = useRef(null);
  const pendingCreatedThreadIdRef = useRef(null);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }

    try {
      window.localStorage.setItem(
        IMAGE_OPTIONS_STORAGE_KEY,
        JSON.stringify(normalizeImageOptions(imageOptions))
      );
    } catch {
      // Ignore storage failures (private mode, quota, disabled storage).
    }
  }, [imageOptions]);

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
  const hasSheetsFileSource = Boolean(sheetsFileAttachment?.id);
  const hasSheetsUrlSource = Boolean(loadedSheetsUrl);
  const sheetsSourceType = hasSheetsFileSource ? "file" : hasSheetsUrlSource ? "google_sheet" : null;
  const validSheetsUrl = isValidGoogleSheetsUrl(sheetsUrlInput);

  const BOTTOM_THRESHOLD_PX = 100;

  const getDistanceFromBottom = useCallback(() => {
    const stream = chatStreamRef.current;
    if (!stream) {
      return 0;
    }

    return stream.scrollHeight - stream.scrollTop - stream.clientHeight;
  }, []);

  const isNearBottom = useCallback(() => {
    const stream = chatStreamRef.current;
    if (!stream) {
      return true;
    }

    const distanceFromBottom = getDistanceFromBottom();
    return distanceFromBottom <= BOTTOM_THRESHOLD_PX;
  }, [getDistanceFromBottom]);

  const scrollToBottom = useCallback((behavior = "auto") => {
    if (!chatBottomRef.current) {
      return;
    }

    chatBottomRef.current.scrollIntoView({
      behavior,
      block: "end",
    });
  }, []);

  const evaluateScrollState = useCallback(() => {
    const stream = chatStreamRef.current;
    if (!stream) {
      shouldAutoScrollRef.current = true;
      setShowJumpToLatest(false);
      return;
    }

    const nearBottom = isNearBottom();
    const distanceFromBottom = getDistanceFromBottom();
    const hasOverflow = Boolean(stream && stream.scrollHeight > stream.clientHeight + 8);

    shouldAutoScrollRef.current = nearBottom;
    setShowJumpToLatest(hasOverflow && distanceFromBottom > BOTTOM_THRESHOLD_PX);
  }, [getDistanceFromBottom, isNearBottom]);

  const handleChatScroll = useCallback(() => {
    evaluateScrollState();
  }, [evaluateScrollState]);

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => evaluateScrollState());
    return () => {
      window.cancelAnimationFrame(frame);
    };
  }, [evaluateScrollState, activeThreadId]);

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

      const optimisticThreadId = optimisticNewThreadIdRef.current;
      if (optimisticThreadId) {
        const currentState = useChatStore.getState();
        const hasOptimistic = currentState.threads.some((candidate) => candidate.id === optimisticThreadId);

        if (hasOptimistic) {
          const reconciledThreads = currentState.threads.map((candidate) =>
            candidate.id === optimisticThreadId ? normalized : candidate
          );
          setThreads(reconciledThreads);
        } else {
          upsertThread(normalized);
        }

        // Always switch to the real thread id created by the server.
        // Relying on the temp id still being active can race with list sync updates.
        setActiveThread(normalized.id);
        pendingCreatedThreadIdRef.current = normalized.id;

        optimisticNewThreadIdRef.current = null;
        optimisticPreviousThreadIdRef.current = null;
      } else {
        upsertThread(normalized);
        setActiveThread(normalized.id);
        pendingCreatedThreadIdRef.current = normalized.id;
      }

      setMessages([]);
      setError("");
      queryClient.invalidateQueries({ queryKey: ["chat-threads", user?.email] });
    },
    onError: (mutationError) => {
      const optimisticThreadId = optimisticNewThreadIdRef.current;
      if (optimisticThreadId) {
        const previousThreadId = optimisticPreviousThreadIdRef.current;
        removeThread(optimisticThreadId);
        setActiveThread(previousThreadId || null);
        optimisticNewThreadIdRef.current = null;
        optimisticPreviousThreadIdRef.current = null;
      }
      pendingCreatedThreadIdRef.current = null;

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
    enabled: Boolean(user?.email && activeThreadId && !String(activeThreadId).startsWith("temp-")),
    staleTime: 15_000,
  });

  const queryLoadError = useMemo(() => {
    if (threadsQuery.isError) {
      return extractApiError(threadsQuery.error, "Unable to load chat threads");
    }
    if (messagesQuery.isError) {
      return extractApiError(messagesQuery.error, "Unable to load this conversation");
    }
    return "";
  }, [messagesQuery.error, messagesQuery.isError, threadsQuery.error, threadsQuery.isError]);

  const displayError = queryLoadError || error;
  const visibleMessages = queryLoadError ? [] : messages;

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

    const currentState = useChatStore.getState();
    let mergedThreads = normalizedThreads;

    // Preserve the currently active thread if the latest list payload has not caught up yet.
    if (activeThreadId && !normalizedThreads.some((thread) => thread.id === activeThreadId)) {
      const localActiveThread = currentState.threads.find((thread) => thread.id === activeThreadId);
      if (localActiveThread) {
        mergedThreads = [
          localActiveThread,
          ...normalizedThreads.filter((thread) => thread.id !== localActiveThread.id),
        ];
      }
    }

    setThreads(mergedThreads);

    const pendingCreatedThreadId = pendingCreatedThreadIdRef.current;
    if (pendingCreatedThreadId && mergedThreads.some((thread) => thread.id === pendingCreatedThreadId)) {
      pendingCreatedThreadIdRef.current = null;
    }

    const hasOptimisticActiveThread = Boolean(
      activeThreadId && String(activeThreadId).startsWith("temp-")
    );
    const hasPendingCreatedActiveThread = Boolean(
      pendingCreatedThreadId && activeThreadId === pendingCreatedThreadId
    );

    let preferredThreadId = null;
    if (typeof window !== "undefined") {
      try {
        preferredThreadId = window.localStorage.getItem(getLastActiveThreadStorageKey(user?.email));
      } catch {
        preferredThreadId = null;
      }
    }

    if (
      !hasOptimisticActiveThread &&
      !hasPendingCreatedActiveThread &&
      (!activeThreadId || !mergedThreads.some((thread) => thread.id === activeThreadId))
    ) {
      const preferred = mergedThreads.find((thread) => thread.id === preferredThreadId);
      setActiveThread(preferred?.id ?? mergedThreads[0]?.id ?? null);
    }
  }, [
    activeThreadId,
    setActiveThread,
    setThreads,
    threadsQuery.data,
    user?.email,
  ]);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }

    const key = getLastActiveThreadStorageKey(user?.email);
    try {
      if (activeThreadId && !String(activeThreadId).startsWith("temp-")) {
        window.localStorage.setItem(key, String(activeThreadId));
      } else if (!activeThreadId) {
        window.localStorage.removeItem(key);
      }
    } catch {
      // Ignore storage errors in private mode/quota-constrained environments.
    }
  }, [activeThreadId, user?.email]);

  useEffect(() => {
    if (!messagesQuery.data) {
      return;
    }

    const normalizedMessages = messagesQuery.data.map(toUiMessage);
    setMessages(normalizedMessages);
  }, [messagesQuery.data, setMessages]);

  useEffect(() => {
    const threadChanged = previousThreadRef.current !== activeThreadId;
    const previousCount = previousMessageCountRef.current;
    const previousLastMessageId = previousLastMessageIdRef.current;
    const previousLastMessageContent = previousLastMessageContentRef.current;
    const lastMessage = messages[messages.length - 1] ?? null;

    previousThreadRef.current = activeThreadId;

    if (threadChanged) {
      shouldAutoScrollRef.current = true;
      setShowJumpToLatest(false);
      pendingInstantScrollRef.current = true;
    }

    const shouldForceInstant = pendingInstantScrollRef.current;

    if (shouldForceInstant) {
      requestAnimationFrame(() => {
        scrollToBottom("auto");
        evaluateScrollState();
      });
      pendingInstantScrollRef.current = false;
    } else if (shouldAutoScrollRef.current) {
      const assistantAppended =
        Boolean(lastMessage) &&
        lastMessage.role === "assistant" &&
        (messages.length > previousCount ||
          (lastMessage.id === previousLastMessageId &&
            lastMessage.content !== previousLastMessageContent));

      requestAnimationFrame(() => {
        scrollToBottom(assistantAppended ? "smooth" : "auto");
        evaluateScrollState();
      });
    }

    previousMessageCountRef.current = messages.length;
    previousLastMessageIdRef.current = lastMessage?.id ?? null;
    previousLastMessageContentRef.current = lastMessage?.content ?? "";
  }, [activeThreadId, evaluateScrollState, messages, scrollToBottom]);

  useEffect(() => {
    if (!threadsQuery.isError) {
      return;
    }

    if (axios.isAxiosError(threadsQuery.error) && threadsQuery.error.response?.status === 401) {
      clearAuth();
      navigate("/auth", { replace: true });
    }
  }, [threadsQuery.error, threadsQuery.isError, clearAuth, navigate]);

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
      const assistantAttachments = (assistantMessage.attachments || []).map(toUiAttachment);

      addMessage(
        {
          id: String(assistantMessage.id || crypto.randomUUID()),
          threadId: assistantMessage.thread_id ? String(assistantMessage.thread_id) : activeThreadId,
          parentMessageId: assistantMessage.parent_message_id
            ? String(assistantMessage.parent_message_id)
            : null,
          role: "assistant",
          content: assistantMessage.content,
          createdAt: assistantMessage.created_at || new Date().toISOString(),
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

  const editMessageMutation = useMutation({
    mutationFn: chatApi.editMessage,
    onSuccess: (payload) => {
      const deletedIds = new Set((payload.deleted_message_ids || []).map((item) => String(item)));
      const updated = toUiMessage(payload.updated_message);
      const regenerated = toUiMessage(payload.new_response);

      const currentMessages = useChatStore.getState().messages;
      const kept = currentMessages.filter((item) => !deletedIds.has(String(item.id)));
      const withoutUpdated = kept.filter((item) => String(item.id) !== updated.id);
      const next = [...withoutUpdated, updated, regenerated].sort(
        (a, b) => new Date(a.createdAt).getTime() - new Date(b.createdAt).getTime()
      );
      setMessages(next);

      setEditingMessageId(null);
      setEditingDraft("");
      setError("");
      queryClient.invalidateQueries({ queryKey: ["chat-threads", user?.email] });
      queryClient.invalidateQueries({ queryKey: ["thread-messages", activeThreadId] });
    },
    onError: (mutationError) => {
      setError(extractApiError(mutationError, "Unable to edit message"));
    },
  });

  const retryMessageMutation = useMutation({
    mutationFn: chatApi.retryMessage,
    onSuccess: (payload) => {
      const deletedIds = new Set((payload.deleted_message_ids || []).map((item) => String(item)));
      const regenerated = toUiMessage(payload.new_response);

      const currentMessages = useChatStore.getState().messages;
      const kept = currentMessages.filter((item) => !deletedIds.has(String(item.id)));
      const next = [...kept, regenerated].sort(
        (a, b) => new Date(a.createdAt).getTime() - new Date(b.createdAt).getTime()
      );
      setMessages(next);

      setError("");
      queryClient.invalidateQueries({ queryKey: ["chat-threads", user?.email] });
      queryClient.invalidateQueries({ queryKey: ["thread-messages", activeThreadId] });
    },
    onError: (mutationError) => {
      setError(extractApiError(mutationError, "Unable to retry message"));
    },
  });

  const handleCreateThread = async () => {
    if (createThreadGuardRef.current || createThreadMutation.isPending) {
      return;
    }

    createThreadGuardRef.current = true;

    const optimisticThreadId = `temp-${crypto.randomUUID()}`;
    const now = new Date().toISOString();
    optimisticNewThreadIdRef.current = optimisticThreadId;
    optimisticPreviousThreadIdRef.current = activeThreadId;

    upsertThread({
      id: optimisticThreadId,
      title: "New chat",
      createdAt: now,
      updatedAt: now,
    });
    setActiveThread(optimisticThreadId);
    setMessages([]);
    setInput("");
    setPendingAttachments([]);
    setSheetsFileAttachment(null);
    setSheetsUrlInput("");
    setLoadedSheetsUrl("");
    setSheetsPreview(null);
    setImageStatus("idle");
    setImageStatusMessage("");
    setLastSendAttempt(null);
    setFailedSend(null);
    setError("");
    shouldAutoScrollRef.current = true;
    setShowJumpToLatest(false);
    pendingInstantScrollRef.current = true;
    requestAnimationFrame(() => scrollToBottom("auto"));

    try {
      await createThreadMutation.mutateAsync({});
    } catch {
      // Error state is already handled in mutation onError.
    } finally {
      createThreadGuardRef.current = false;
    }
  };

  const resolveThreadIdForServer = useCallback(async (candidateThreadId = null) => {
    let destinationThreadId = candidateThreadId || activeThreadId;

    if (destinationThreadId && !String(destinationThreadId).startsWith("temp-")) {
      return String(destinationThreadId);
    }

    // If create-thread is already in flight, wait briefly for temp->real id reconciliation.
    if (createThreadMutation.isPending) {
      const deadline = Date.now() + 1_500;
      while (Date.now() < deadline) {
        const currentId = useChatStore.getState().activeThreadId;
        if (currentId && !String(currentId).startsWith("temp-")) {
          return String(currentId);
        }
        await new Promise((resolve) => setTimeout(resolve, 50));
      }
    }

    const createdThread = await createThreadMutation.mutateAsync({});
    return String(createdThread.id);
  }, [activeThreadId, createThreadMutation]);

  const uploadFilesToThread = async (threadId, files) => {
    const destinationThreadId = await resolveThreadIdForServer(threadId);
    const localItems = files.map((file) => buildLocalAttachment(file));
    setPendingAttachments((current) => [...current, ...localItems]);

    await Promise.all(
      files.map(async (file, index) => {
        const local = localItems[index];

        try {
          const uploaded = await uploadAttachmentMutation.mutateAsync({
            threadId: destinationThreadId,
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
      const destinationThreadId = await resolveThreadIdForServer(activeThreadId);

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

  const handleClearSheetsFile = useCallback(() => {
    setSheetsFileAttachment(null);
    setSheetsPreview(null);
    setError("");
  }, []);

  const handleClearSheetsUrl = useCallback(() => {
    setLoadedSheetsUrl("");
    setSheetsPreview(null);
    setError("");
  }, []);

  const loadSheetsPreviewFromPayload = useCallback(async ({ fileId = null, sheetUrl = null }) => {
    setLoadingSheetsPreview(true);
    try {
      const preview = await sheetsApi.loadPreview({ fileId, sheetUrl });
      setSheetsPreview(preview);
      setError("");
      return preview;
    } catch (previewError) {
      setSheetsPreview(null);
      throw previewError;
    } finally {
      setLoadingSheetsPreview(false);
    }
  }, []);

  const handlePickSheetsFiles = useCallback(async (files) => {
    const selected = Array.isArray(files) ? files[0] : null;
    if (!selected) {
      return;
    }

    const supported = /\.(csv|xlsx)$/i.test(selected.name);
    if (!supported) {
      setError("Unsupported file type. Please upload a .csv or .xlsx file.");
      return;
    }

    try {
      const destinationThreadId = await resolveThreadIdForServer(activeThreadId);

      const local = buildLocalAttachment(selected);
      setSheetsFileAttachment({
        ...local,
      });

      const uploaded = await uploadAttachmentMutation.mutateAsync({
        threadId: destinationThreadId,
        file: selected,
        onUploadProgress: (event) => {
          const nextProgress = event.total
            ? Math.round((event.loaded / event.total) * 100)
            : 0;
          setSheetsFileAttachment((current) =>
            current
              ? {
                  ...current,
                  progress: nextProgress,
                }
              : current
          );
        },
      });

      const normalizedAttachment = {
        id: String(uploaded.id),
        file_name: uploaded.file_name,
        file_type: uploaded.file_type,
        file_size: uploaded.file_size,
        created_at: uploaded.created_at,
        metadata: uploaded.metadata || {},
        uploading: false,
        progress: 100,
      };

      setSheetsFileAttachment(normalizedAttachment);
      setLoadedSheetsUrl("");
      await loadSheetsPreviewFromPayload({ fileId: normalizedAttachment.id, sheetUrl: null });
    } catch (mutationError) {
      setSheetsFileAttachment(null);
      setError(extractApiError(mutationError, "Unable to upload and preview sheet"));
    }
  }, [
    activeThreadId,
    createThreadMutation,
    loadSheetsPreviewFromPayload,
    resolveThreadIdForServer,
    uploadAttachmentMutation,
  ]);

  const handleLoadSheetsUrl = useCallback(async () => {
    const url = sheetsUrlInput.trim();
    if (!isValidGoogleSheetsUrl(url)) {
      setError("Invalid Google Sheets URL. Use a URL like docs.google.com/spreadsheets/d/...");
      return;
    }

    try {
      setSheetsFileAttachment(null);
      setLoadedSheetsUrl(url);
      await loadSheetsPreviewFromPayload({ fileId: null, sheetUrl: url });
      setError("");
    } catch (previewError) {
      setLoadedSheetsUrl("");
      setError(extractApiError(previewError, "Unable to load Google Sheet preview"));
    }
  }, [loadSheetsPreviewFromPayload, sheetsUrlInput]);

  const handleToggleDbQueryMode = useCallback(() => {
    setDbQueryMode((previous) => !previous);
    if (!dbQueryMode) {
      setSheetsQueryMode(false);
    }
  }, [dbQueryMode]);

  const handleToggleSheetsQueryMode = useCallback(() => {
    setSheetsQueryMode((previous) => !previous);
    if (!sheetsQueryMode) {
      setDbQueryMode(false);
    }
  }, [sheetsQueryMode]);

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

  const handleStartEditMessage = (message) => {
    setEditingMessageId(message.id);
    setEditingDraft(message.content || "");
  };

  const handleCancelEditMessage = () => {
    setEditingMessageId(null);
    setEditingDraft("");
  };

  const handleSubmitEditMessage = async (messageId) => {
    if (!activeThreadId || !String(editingDraft || "").trim()) {
      return;
    }

    await editMessageMutation.mutateAsync({
      threadId: activeThreadId,
      messageId,
      content: editingDraft.trim(),
    });
  };

  const handleRetryAssistantMessage = async (messageId) => {
    if (!activeThreadId) {
      return;
    }

    await retryMessageMutation.mutateAsync({
      threadId: activeThreadId,
      messageId,
    });
  };

  const handleSend = async () => {
    const content = input.trim();
    const inferredFormulaText = inferFormulaText(content);
    const canSend = content || pendingAttachments.length > 0;
    const busy =
      sendMutation.isPending ||
      createThreadMutation.isPending ||
      uploading ||
      loadingSheetsPreview ||
      sheetsQuerying;

    if (busy) {
      return;
    }

    if (sheetsQueryMode) {
      const question = sanitizeSheetsQuestion(content);
      if (!question) {
        setError("Please enter a question about your sheet.");
        return;
      }

      if (!hasSheetsFileSource && !hasSheetsUrlSource) {
        setError("Load a CSV/XLSX file or Google Sheet before asking questions.");
        return;
      }

      const now = Date.now();
      if (now - lastSheetsQueryAt < SHEETS_QUERY_RATE_LIMIT_MS) {
        setError("You're asking questions too quickly. Please wait a moment and try again.");
        return;
      }

      setLastSheetsQueryAt(now);

      try {
        let destinationThreadId = activeThreadId;
        if (!destinationThreadId) {
          const createdThread = await createThreadMutation.mutateAsync({});
          destinationThreadId = String(createdThread.id);
        }

        const localUserMessage = {
          ...buildLocalMessage({ role: "user", content: question }),
          attachments: [],
          metadata: {
            mode: "sheets",
          },
        };

        addMessage(localUserMessage);
        setInput("");
        setError("");
        setSheetsQuerying(true);
        shouldAutoScrollRef.current = true;
        setShowJumpToLatest(false);
        pendingInstantScrollRef.current = true;
        requestAnimationFrame(() => scrollToBottom("auto"));

        const sheetsResponse = hasSheetsFileSource
          ? await sheetsApi.queryFile({
              fileId: sheetsFileAttachment.id,
              question,
              chatThreadId: destinationThreadId,
            })
          : await sheetsApi.queryGoogleSheet({
              sheetUrl: loadedSheetsUrl,
              question,
              chatThreadId: destinationThreadId,
            });

        const persistedThreadId = sheetsResponse?.thread_id
          ? String(sheetsResponse.thread_id)
          : destinationThreadId;

        if (persistedThreadId && persistedThreadId !== activeThreadId) {
          setActiveThread(persistedThreadId);
        }

        addMessage({
          ...buildLocalMessage({
            role: "assistant",
            content: sheetsResponse?.answer || "No answer returned.",
          }),
          attachments: [],
          metadata: {
            mode: "sheets",
            intermediate_steps: sheetsResponse?.intermediate_steps || [],
          },
        });

        setError("");
        queryClient.invalidateQueries({ queryKey: ["chat-threads", user?.email] });
        queryClient.invalidateQueries({ queryKey: ["thread-messages", persistedThreadId] });

        // Optional side-effect: trigger n8n email workflow through backend relay.
        // Failures are intentionally non-blocking so core sheets Q&A stays unaffected.
        if (hasSheetsFileSource && sheetsFileAttachment?.id) {
          workflowsApi
            .triggerSheetsEmailRun({
              fileId: sheetsFileAttachment.id,
              question,
              chatThreadId: persistedThreadId,
              recipientEmail: user?.email || null,
            })
            .catch((workflowError) => {
              console.warn("Sheets email workflow trigger failed", workflowError);
            });
        }
      } catch (sheetsError) {
        setError(
          extractApiError(
            sheetsError,
            "Unable to process your sheets query. Try reducing file size or simplifying the question."
          )
        );

        if (axios.isAxiosError(sheetsError) && sheetsError.response?.status === 401) {
          clearAuth();
          navigate("/auth", { replace: true });
        }
      } finally {
        setSheetsQuerying(false);
      }

      return;
    }

    if (!canSend) {
      return;
    }

    try {
      let destinationThreadId = activeThreadId;
      if (!destinationThreadId) {
        const createdThread = await createThreadMutation.mutateAsync({});
        destinationThreadId = String(createdThread.id);
      }

      const readyAttachments = pendingAttachments.filter((item) => item.id && !item.uploading);
      const messageAttachments = readyAttachments.map(toUiAttachment);

      const localUserMessage = {
        ...buildLocalMessage({ role: "user", content: content || "[attachment/formula message]" }),
        attachments: messageAttachments,
      };
      addMessage(localUserMessage);

      const payload = {
        threadId: destinationThreadId,
        message: content || "Please process the attached content.",
        attachmentIds: readyAttachments.map((item) => item.id),
        formulaText: inferredFormulaText,
        dbQueryMode,
        numImages: imageOptions.numImages,
        aspectRatio: imageOptions.aspectRatio,
        negativePrompt: imageOptions.negativePrompt.trim() || null,
        enhancePrompt: imageOptions.enhancePrompt,
      };

      if (!dbQueryMode && hasLikelyImageIntent(payload.message)) {
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
      shouldAutoScrollRef.current = true;
      setShowJumpToLatest(false);
      pendingInstantScrollRef.current = true;
      requestAnimationFrame(() => scrollToBottom("auto"));
      sendMutation.mutate(payload);
    } catch {
      // Error state is already handled in mutation onError.
    }
  };

  const handleJumpToLatest = () => {
    shouldAutoScrollRef.current = true;
    setShowJumpToLatest(false);
    pendingInstantScrollRef.current = true;
    scrollToBottom("auto");
  };

  const handleRetryFailedPrompt = () => {
    if (!failedSend || sendMutation.isPending || createThreadMutation.isPending || uploading) {
      return;
    }

    if (!failedSend.payload?.dbQueryMode && hasLikelyImageIntent(failedSend.payload?.message || "")) {
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
          </div>

          <button
            className="primary-btn threads-panel__new-btn"
            type="button"
            onClick={handleCreateThread}
            disabled={createThreadMutation.isPending}
          >
            {createThreadMutation.isPending ? "Creating..." : "New chat"}
          </button>

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
                    shouldAutoScrollRef.current = true;
                    setActiveThread(thread.id);
                    setMessages([]);
                    setPendingAttachments([]);
                    setSheetsFileAttachment(null);
                    setSheetsUrlInput("");
                    setLoadedSheetsUrl("");
                    setSheetsPreview(null);
                    setError("");
                  }}
                >
                  {thread.title}
                </button>
                <div className="thread-item__actions">
                  <button className="thread-action" type="button" onClick={() => handleRenameThread(thread)}>
                    Rename
                  </button>
                  <button className="thread-action thread-action--danger" type="button" onClick={() => handleDeleteThread(thread)}>
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
          <div className="chat-header__top-row">
            <h1>Hello, {greeting}</h1>
            <div className="chat-header__actions">
              <button
                className="secondary-btn chat-header__digest-btn"
                type="button"
                onClick={() => navigate("/research-digest")}
              >
                Research Digest Agent
              </button>
              <button
                className="secondary-btn chat-header__digest-btn"
                type="button"
                onClick={() => navigate("/tic-tac-toe")}
              >
                Tic Tac Toe
              </button>
            </div>
          </div>
          <p>Ask a question and your assistant will respond with context-aware guidance.</p>
        </header>

        <div className="chat-stream" ref={chatStreamRef} onScroll={handleChatScroll}>
          {activeThreadId && messagesQuery.isFetching ? (
            <div className="empty-state">
              <p>Loading conversation...</p>
            </div>
          ) : visibleMessages.length === 0 ? (
            <div className="empty-state">
              <p>Start with something simple.</p>
              <p className="muted">Example: Summarize today's priorities in 5 bullet points.</p>
            </div>
          ) : (
            visibleMessages.map((message) => (
              <MessageBubble
                key={message.id}
                message={message}
                canEdit={
                  message.role === "user" &&
                  isLikelyPersistedMessage(message, activeThreadId) &&
                  !sendMutation.isPending &&
                  !retryMessageMutation.isPending
                }
                editing={editingMessageId === message.id}
                editingValue={editingMessageId === message.id ? editingDraft : message.content}
                onEditingChange={setEditingDraft}
                onStartEdit={() => handleStartEditMessage(message)}
                onCancelEdit={handleCancelEditMessage}
                onSubmitEdit={() => handleSubmitEditMessage(message.id)}
                editSubmitting={editMessageMutation.isPending && editingMessageId === message.id}
                canRetry={
                  (message.role === "assistant" &&
                    isLikelyPersistedMessage(message, activeThreadId)) ||
                  failedSend?.messageId === message.id
                }
                onRetry={() =>
                  failedSend?.messageId === message.id
                    ? handleRetryFailedPrompt()
                    : handleRetryAssistantMessage(message.id)
                }
                retrying={sendMutation.isPending || retryMessageMutation.isPending}
              />
            ))
          )}
          <div ref={chatBottomRef} aria-hidden="true" />
        </div>

        {showJumpToLatest ? (
          <button
            type="button"
            className="chat-jump-latest"
            onClick={handleJumpToLatest}
            aria-label="Jump to latest message"
            title="Jump to latest message"
          >
            <ChevronDown className="chat-jump-latest__icon" aria-hidden="true" />
          </button>
        ) : null}

        {imageStatus !== "idle" ? (
          <p className={`chat-image-status chat-image-status--${imageStatus}`}>{imageStatusMessage}</p>
        ) : null}

        {displayError ? <p className="error-text chat-error">{displayError}</p> : null}

        <ChatComposer
          focusKey={activeThreadId || "new-chat"}
          value={input}
          onChange={setInput}
          onSend={handleSend}
          sending={sendMutation.isPending || sheetsQuerying}
          attachments={pendingAttachments}
          onPickFiles={handlePickFiles}
          onRemoveAttachment={handleRemoveAttachment}
          uploading={uploading || createThreadMutation.isPending}
          imageOptions={imageOptions}
          onImageOptionsChange={setImageOptions}
          dbQueryMode={dbQueryMode}
          onToggleDbQueryMode={handleToggleDbQueryMode}
          sheetsQueryMode={sheetsQueryMode}
          onToggleSheetsQueryMode={handleToggleSheetsQueryMode}
          sheetsFile={sheetsFileAttachment}
          onPickSheetsFiles={handlePickSheetsFiles}
          onClearSheetsFile={handleClearSheetsFile}
          sheetsUrlValue={sheetsUrlInput}
          onChangeSheetsUrlValue={setSheetsUrlInput}
          onLoadSheetsUrl={handleLoadSheetsUrl}
          onClearSheetsUrl={handleClearSheetsUrl}
          sheetsUrlIsValid={validSheetsUrl}
          sheetsPreview={sheetsPreview}
          loadingSheetsPreview={loadingSheetsPreview}
          sheetsSourceType={sheetsSourceType}
        />
      </section>
    </main>
  );
}
