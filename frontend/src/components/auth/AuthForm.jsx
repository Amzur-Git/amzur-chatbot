export default function AuthForm({
  mode,
  form,
  onChange,
  onSubmit,
  onGoogleSignIn,
  loading,
  error,
  onModeChange,
}) {
  const isRegister = mode === "register";

  return (
    <section className="auth-panel">
      <div className="auth-panel__header">
        <p className="eyebrow">amzur ai</p>
        <h1>{isRegister ? "Create your account" : "Welcome back"}</h1>
        <p className="muted">
          {isRegister
            ? "Register to start chatting with your assistant."
            : "Login to continue your workspace conversation."}
        </p>
      </div>

      <form className="auth-form" onSubmit={onSubmit}>
        <button
          className="secondary-btn"
          type="button"
          onClick={onGoogleSignIn}
          disabled={loading}
        >
          Continue with Google
        </button>

        <div className="auth-divider" aria-hidden="true">
          <span>or</span>
        </div>

        {isRegister ? (
          <label className="field">
            <span>Full name</span>
            <input
              type="text"
              name="fullName"
              placeholder="Alex Morgan"
              value={form.fullName}
              onChange={onChange}
              autoComplete="name"
            />
          </label>
        ) : null}

        <label className="field">
          <span>Email</span>
          <input
            type="email"
            name="email"
            placeholder="you@company.com"
            value={form.email}
            onChange={onChange}
            required
            autoComplete="email"
          />
        </label>

        <label className="field">
          <span>Password</span>
          <input
            type="password"
            name="password"
            placeholder="Enter password"
            value={form.password}
            onChange={onChange}
            required
            autoComplete={isRegister ? "new-password" : "current-password"}
          />
        </label>

        {error ? <p className="error-text">{error}</p> : null}

        <button className="primary-btn" type="submit" disabled={loading}>
          {loading
            ? "Please wait..."
            : isRegister
            ? "Create account"
            : "Sign in"}
        </button>
      </form>

      <div className="auth-panel__footer">
        <button
          className="text-btn"
          type="button"
          onClick={() => onModeChange(isRegister ? "login" : "register")}
          disabled={loading}
        >
          {isRegister
            ? "Already have an account? Sign in"
            : "New here? Create an account"}
        </button>
      </div>
    </section>
  );
}
