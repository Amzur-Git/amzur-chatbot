import { create } from "zustand";
import { persist } from "zustand/middleware";

export const useAuthStore = create(
  persist(
    (set) => ({
      isAuthenticated: false,
      user: null,
      accessToken: null,
      setAuth: ({ user, accessToken }) =>
        set((state) => ({
          isAuthenticated: true,
          user,
          accessToken: accessToken ?? state.accessToken ?? null,
        })),
      clearAuth: () =>
        set({
          isAuthenticated: false,
          user: null,
          accessToken: null,
        }),
    }),
    {
      name: "amzur-auth",
      partialize: (state) => ({
        isAuthenticated: state.isAuthenticated,
        user: state.user,
        accessToken: state.accessToken,
      }),
    }
  )
);
