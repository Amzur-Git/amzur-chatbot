import axios from "axios";

const defaultApiBaseUrl =
  typeof window !== "undefined"
    ? `${window.location.protocol}//${window.location.hostname}:8000`
    : "http://127.0.0.1:8000";

export const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL ?? defaultApiBaseUrl;

export const apiClient = axios.create({
  baseURL: API_BASE_URL,
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

  googleLoginUrl: () => `${API_BASE_URL}/api/auth/google/login`,
};

export const chatApi = {
  getHistory: async () => {
    const response = await apiClient.get("/api/chat/history");
    return response.data;
  },

  sendMessage: async ({ message }) => {
    const response = await apiClient.post("/api/chat/send", { message });
    return response.data;
  },
};
