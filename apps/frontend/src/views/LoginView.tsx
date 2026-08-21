// Login: username + password -> POST /login -> the session brick adopts the token.

import { useState } from "react";

import { startSession } from "../auth";
import { Button } from "../components/Button";
import { Icon } from "../components/Icon";
import { FormCard, TextField } from "../components/forms";
import { ApiError, login } from "../lib/api";

export function LoginView() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const ready = username.trim().length > 0 && password.length > 0;

  async function submit() {
    if (pending || !ready) return;
    setPending(true);
    setError(null);
    try {
      const token = await login(username.trim(), password);
      if (!startSession(token)) setError("The server returned a token this client cannot read.");
    } catch (cause) {
      setError(
        cause instanceof ApiError ? cause.message : "Cannot reach the backend. Is it running?",
      );
    } finally {
      setPending(false);
    }
  }

  return (
    <FormCard
      title="Sign in"
      subtitle="Your tenant comes from your account. Every answer is scoped to it server-side."
      error={error}
      onSubmit={submit}
    >
      <TextField
        id="login-username"
        label="Username"
        value={username}
        onChange={setUsername}
        autoComplete="username"
        autoFocus
        disabled={pending}
      />
      <TextField
        id="login-password"
        label="Password"
        type="password"
        value={password}
        onChange={setPassword}
        autoComplete="current-password"
        disabled={pending}
      />

      <Button variant="primary" type="submit" className="btn-block" disabled={pending || !ready}>
        <Icon
          name={pending ? "loader" : "check"}
          size={16}
          className={pending ? "loader-spin" : undefined}
        />
        {pending ? "Signing in..." : "Sign in"}
      </Button>
    </FormCard>
  );
}
