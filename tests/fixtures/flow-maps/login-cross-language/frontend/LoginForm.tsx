export interface Session {
  token: string;
}

export async function submitLogin(
  email: string,
  password: string,
): Promise<Session> {
  const response = await fetch("/api/session", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  if (!response.ok) {
    throw new Error("Login failed");
  }
  return (await response.json()) as Session;
}
