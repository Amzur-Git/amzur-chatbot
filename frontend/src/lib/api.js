import axios from "axios";

function normalizeBaseUrl(url) {
  return String(url || "").replace(/\/+$/, "");
}

function normalizeDevHostname(hostname) {
  if (hostname === "0.0.0.0") {
    return "localhost";
  }
  return hostname;
}

function alignLoopbackHostname(url) {
  if (typeof window === "undefined") {
    return normalizeBaseUrl(url);
  }

  try {
    const parsed = new URL(url);
    const pageHostname = normalizeDevHostname(window.location.hostname);
    const loopbackHostnames = new Set(["localhost", "127.0.0.1", "0.0.0.0"]);

    if (
      loopbackHostnames.has(parsed.hostname) &&
      loopbackHostnames.has(pageHostname) &&
      parsed.hostname !== pageHostname
    ) {
      parsed.hostname = pageHostname;
      return normalizeBaseUrl(parsed.toString());
    }
  } catch {
    // If URL parsing fails, fall back to raw normalization.
  }

  return normalizeBaseUrl(url);
}

function getConfiguredApiBaseUrl() {
  const configured = import.meta.env.VITE_API_BASE_URL;
  if (configured) {
    return alignLoopbackHostname(configured);
  }

  if (typeof window !== "undefined") {
    const host = normalizeDevHostname(window.location.hostname);
    return normalizeBaseUrl(`${window.location.protocol}//${host}:8001`);
  }

  return "http://127.0.0.1:8001";
}

const currentApiBaseUrl = getConfiguredApiBaseUrl();

export const API_BASE_URL = currentApiBaseUrl;

export function getApiBaseUrl() {
  return currentApiBaseUrl;
}

export const apiClient = axios.create({
  baseURL: currentApiBaseUrl,
  withCredentials: true,
});

apiClient.interceptors.request.use((config) => {
  if (typeof FormData !== "undefined" && config.data instanceof FormData) {
    if (config.headers) {
      delete config.headers["Content-Type"];
      delete config.headers["content-type"];
    }
  }

  return config;
});

export function extractApiError(error, fallbackMessage = "Something went wrong") {
  if (axios.isAxiosError(error)) {
    const detail = error.response?.data?.detail;
    if (typeof detail === "string" && detail.trim()) {
      return detail;
    }
    if (Array.isArray(detail) && detail.length > 0) {
      const normalized = detail
        .map((item) => {
          if (!item || typeof item !== "object") {
            return "";
          }

          const location = Array.isArray(item.loc) ? item.loc.join(".") : "";
          const message = typeof item.msg === "string" ? item.msg : "";

          if (location && message) {
            return `${location}: ${message}`;
          }
          return message || "";
        })
        .filter(Boolean)
        .join("; ");

      if (normalized) {
        return normalized;
      }
    }
    if (error.message) {
      return error.message;
    }
  }
  return fallbackMessage;
}

function isNetworkLevelAxiosError(error) {
  return axios.isAxiosError(error) && !error.response;
}

function getLoopbackFallbackBaseUrls(baseUrl) {
  try {
    const parsed = new URL(baseUrl);
    const protocol = parsed.protocol || "http:";
    const port = parsed.port || "8001";

    const candidates = [`${protocol}//localhost:${port}`, `${protocol}//127.0.0.1:${port}`];

    return candidates.filter((candidate) => normalizeBaseUrl(candidate) !== normalizeBaseUrl(baseUrl));
  } catch {
    return ["http://localhost:8001", "http://127.0.0.1:8001"].filter(
      (candidate) => normalizeBaseUrl(candidate) !== normalizeBaseUrl(baseUrl)
    );
  }
}

function getPersistedAccessToken() {
  if (typeof window === "undefined") {
    return null;
  }

  try {
    const raw = window.localStorage.getItem("amzur-auth");
    if (!raw) {
      return null;
    }

    const parsed = JSON.parse(raw);
    const token = parsed?.state?.accessToken;
    return typeof token === "string" && token.trim() ? token.trim() : null;
  } catch {
    return null;
  }
}

export const authApi = {
  register: async ({ email, password, fullName }) => {
    const response = await apiClient.post("/api/auth/register", {
      email,
      password,
      full_name: fullName || null,
    });
    return response.data;
  },

  login: async ({ email, password }) => {
    const response = await apiClient.post("/api/auth/login", {
      email,
      password,
    });
    return response.data;
  },

  me: async () => {
    const response = await apiClient.get("/api/auth/me");
    return response.data;
  },

  logout: async () => {
    const response = await apiClient.post("/api/auth/logout");
    return response.data;
  },

  googleLoginUrl: () => {
    const params = new URLSearchParams();
    if (typeof window !== "undefined") {
      params.set("frontend_url", window.location.origin);
    }
    return `${currentApiBaseUrl}/api/auth/google/login?${params.toString()}`;
  },
};

export const chatApi = {
  getThreads: async () => {
    const response = await apiClient.get("/api/chat/threads");
    return response.data;
  },

  createThread: async ({ title } = {}) => {
    const response = await apiClient.post("/api/chat/threads", {
      title: title ?? null,
    });
    return response.data;
  },

  renameThread: async ({ threadId, title }) => {
    const response = await apiClient.patch(`/api/chat/threads/${threadId}`, { title });
    return response.data;
  },

  deleteThread: async ({ threadId }) => {
    const response = await apiClient.delete(`/api/chat/threads/${threadId}`);
    return response.data;
  },

  getThreadMessages: async ({ threadId }) => {
    const response = await apiClient.get(`/api/chat/threads/${threadId}/messages`);
    return response.data;
  },

  getHistory: async () => {
    const response = await apiClient.get("/api/chat/history");
    return response.data;
  },

  sendMessage: async ({
    threadId,
    message,
    attachmentIds = [],
    formulaText = null,
    dbQueryMode = false,
    numImages = null,
    aspectRatio = null,
    negativePrompt = null,
    enhancePrompt = true,
  }) => {
    const payload = {
      message,
      attachment_ids: attachmentIds,
      formula_text: formulaText,
      db_query_mode: dbQueryMode,
      num_images: numImages,
      aspect_ratio: aspectRatio,
      negative_prompt: negativePrompt,
      enhance_prompt: enhancePrompt,
    };

    if (threadId) {
      const response = await apiClient.post(`/api/chat/threads/${threadId}/send`, payload);
      return response.data;
    }

    const response = await apiClient.post("/api/chat/send", payload);
    return response.data;
  },

  editMessage: async ({ threadId, messageId, content }) => {
    const response = await apiClient.put(`/api/chat/messages/${messageId}/edit`, {
      chat_thread_id: threadId,
      content,
    });
    return response.data;
  },

  retryMessage: async ({ threadId, messageId }) => {
    const response = await apiClient.post(`/api/chat/messages/${messageId}/retry`, {
      chat_thread_id: threadId,
    });
    return response.data;
  },

  generateImage: async ({
    prompt,
    chatThreadId,
    numImages = 1,
    aspectRatio = null,
    negativePrompt = null,
    enhancePrompt = true,
  }) => {
    const response = await apiClient.post("/api/generate-image", {
      prompt,
      chat_thread_id: chatThreadId,
      num_images: numImages,
      aspect_ratio: aspectRatio,
      negative_prompt: negativePrompt,
      enhance_prompt: enhancePrompt,
    });
    return response.data;
  },
};

export const attachmentsApi = {
  upload: async ({ threadId, file, onUploadProgress }) => {
    const formData = new FormData();
    formData.append("file", file);

    // Use a dedicated axios call for multipart uploads so shared JSON defaults
    // or interceptors cannot interfere with FormData boundaries.
    const primaryBaseUrl = getApiBaseUrl();
    const accessToken = getPersistedAccessToken();
    const attemptUpload = (baseUrl) =>
      axios.post(`${baseUrl}/api/attachments/upload`, formData, {
        withCredentials: true,
        headers: accessToken
          ? {
              Authorization: `Bearer ${accessToken}`,
            }
          : undefined,
        params: {
          thread_id: threadId,
        },
        onUploadProgress,
      });

    try {
      const response = await attemptUpload(primaryBaseUrl);
      return response.data;
    } catch (error) {
      if (!isNetworkLevelAxiosError(error)) {
        throw error;
      }

      const fallbackBaseUrls = getLoopbackFallbackBaseUrls(primaryBaseUrl);

      for (const fallbackBaseUrl of fallbackBaseUrls) {
        try {
          const retryResponse = await attemptUpload(fallbackBaseUrl);
          return retryResponse.data;
        } catch (retryError) {
          if (!isNetworkLevelAxiosError(retryError)) {
            throw retryError;
          }
        }
      }

      throw error;
    }
  },

  metadata: async ({ attachmentId }) => {
    const response = await apiClient.get(`/api/attachments/${attachmentId}`);
    return response.data;
  },

  delete: async ({ attachmentId }) => {
    const response = await apiClient.delete(`/api/attachments/${attachmentId}`);
    return response.data;
  },

  downloadUrl: (attachmentId) => `${getApiBaseUrl()}/api/attachments/${attachmentId}/download`,
};

const SHEETS_API_TIMEOUT_MS = 180_000;

export const sheetsApi = {
  loadPreview: async ({ sheetUrl = null, fileId = null }) => {
    const response = await apiClient.post(
      "/api/sheets/load-preview",
      {
        sheet_url: sheetUrl,
        file_id: fileId,
      },
      {
        timeout: SHEETS_API_TIMEOUT_MS,
      }
    );
    return response.data;
  },

  queryFile: async ({ fileId, question, chatThreadId = null }) => {
    const response = await apiClient.post(
      "/api/sheets/query-file",
      {
        file_id: fileId,
        question,
        chat_thread_id: chatThreadId,
      },
      {
        timeout: SHEETS_API_TIMEOUT_MS,
      }
    );
    return response.data;
  },

  queryGoogleSheet: async ({ sheetUrl, question, chatThreadId = null }) => {
    const response = await apiClient.post(
      "/api/sheets/query-google-sheet",
      {
        sheet_url: sheetUrl,
        question,
        chat_thread_id: chatThreadId,
      },
      {
        timeout: SHEETS_API_TIMEOUT_MS,
      }
    );
    return response.data;
  },
};

export const workflowsApi = {
  triggerSheetsEmailRun: async ({ fileId, question, chatThreadId = null, recipientEmail = null }) => {
    const response = await apiClient.post("/api/workflows/sheets-email-run", {
      file_id: fileId,
      question,
      chat_thread_id: chatThreadId,
      recipient_email: recipientEmail,
    });
    return response.data;
  },
};
