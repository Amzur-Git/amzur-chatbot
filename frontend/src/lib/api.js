import axios from "axios";

function normalizeBaseUrl(url) {
  return String(url || "").replace(/\/+$/, "");
}

function alignLoopbackHostname(url) {
  if (typeof window === "undefined") {
    return normalizeBaseUrl(url);
  }

  try {
    const parsed = new URL(url);
    const pageHostname = window.location.hostname;
    const loopbackHostnames = new Set(["localhost", "127.0.0.1"]);

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
    return normalizeBaseUrl(`${window.location.protocol}//${window.location.hostname}:8000`);
  }

  return "http://127.0.0.1:8000";
}

const currentApiBaseUrl = getConfiguredApiBaseUrl();

export const API_BASE_URL = currentApiBaseUrl;

export function getApiBaseUrl() {
  return currentApiBaseUrl;
}

export const apiClient = axios.create({
  baseURL: currentApiBaseUrl,
  withCredentials: true,
  headers: {
    "Content-Type": "application/json",
  },
});

export function extractApiError(error, fallbackMessage = "Something went wrong") {
  if (axios.isAxiosError(error)) {
    const detail = error.response?.data?.detail;
    if (typeof detail === "string" && detail.trim()) {
      return detail;
    }
    if (error.message) {
      return error.message;
    }
  }
  return fallbackMessage;
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

    const response = await apiClient.post(`/api/attachments/upload?thread_id=${threadId}`, formData, {
      headers: {
        "Content-Type": "multipart/form-data",
      },
      onUploadProgress,
    });
    return response.data;
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
