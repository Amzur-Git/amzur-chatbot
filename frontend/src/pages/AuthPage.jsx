import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useMutation } from "@tanstack/react-query";
import AuthForm from "../components/auth/AuthForm";
import { authApi, extractApiError } from "../lib/api";
import { useAuthStore } from "../hooks/useAuthStore";

const initialForm = {
  fullName: "",
  email: "",
  password: "",
};

export default function AuthPage() {
  const [mode, setMode] = useState("login");
  const [form, setForm] = useState(initialForm);
  const [error, setError] = useState("");
  const [searchParams] = useSearchParams();

  const setAuth = useAuthStore((state) => state.setAuth);

  const oauthError = useMemo(() => {
    const value = searchParams.get("error");
    if (!value) {
      return "";
    }

    if (value === "google_oauth_not_configured") {
      return "Google sign-in is not configured yet.";
    }
    if (value === "invalid_oauth_state") {
      return "Google sign-in state validation failed. Please try again.";
    }
    return "Google sign-in failed. Please try again.";
  }, [searchParams]);

  const mapUser = (user) => ({
    email: user.email,
    fullName: user.full_name ?? null,
  });

  const loginMutation = useMutation({
    mutationFn: authApi.login,
    onSuccess: (data) => {
      setAuth({
        user: mapUser(data),
        accessToken: data.access_token ?? null,
      });
      setError("");
    },
    onError: (mutationError) => {
      setError(extractApiError(mutationError, "Unable to sign in"));
    },
  });

  const registerMutation = useMutation({
    mutationFn: authApi.register,
    onSuccess: (_, variables) => {
      loginMutation.mutate({
        email: variables.email,
        password: variables.password,
      });
    },
    onError: (mutationError) => {
      setError(extractApiError(mutationError, "Unable to register"));
    },
  });

  const handleChange = (event) => {
    const { name, value } = event.target;
    setForm((previous) => ({ ...previous, [name]: value }));
  };

  const handleSubmit = (event) => {
    event.preventDefault();
    setError("");

    if (mode === "register") {
      registerMutation.mutate({
        email: form.email,
        password: form.password,
        fullName: form.fullName,
      });
      return;
    }

    loginMutation.mutate({
      email: form.email,
      password: form.password,
    });
  };

  const handleGoogleSignIn = () => {
    window.location.assign(authApi.googleLoginUrl());
  };

  const isLoading = registerMutation.isPending || loginMutation.isPending;
  const displayError = error || oauthError;

  return (
    <main className="auth-layout">
      <div className="backdrop-shape backdrop-shape--one" />
      <div className="backdrop-shape backdrop-shape--two" />

      <AuthForm
        mode={mode}
        form={form}
        onChange={handleChange}
        onSubmit={handleSubmit}
        onGoogleSignIn={handleGoogleSignIn}
        loading={isLoading}
        error={displayError}
        onModeChange={setMode}
      />
    </main>
  );
}
