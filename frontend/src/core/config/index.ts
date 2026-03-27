import { env } from "@/env";

function isLoopbackHostname(hostname: string) {
  return (
    hostname === "localhost" ||
    hostname === "127.0.0.1" ||
    hostname === "0.0.0.0" ||
    hostname === "::1"
  );
}

function resolveClientFacingBaseURL(value?: string) {
  if (!value) {
    return "";
  }
  if (typeof window === "undefined") {
    return value;
  }

  try {
    const configured = new URL(value, window.location.origin);
    const current = new URL(window.location.origin);
    if (
      isLoopbackHostname(configured.hostname) &&
      !isLoopbackHostname(current.hostname)
    ) {
      return "";
    }
    return configured.origin;
  } catch {
    return value;
  }
}

export function getBackendBaseURL() {
  const resolved = resolveClientFacingBaseURL(env.NEXT_PUBLIC_BACKEND_BASE_URL);
  if (resolved) {
    return resolved;
  } else {
    return "";
  }
}

export function getLangGraphBaseURL(isMock?: boolean) {
  const resolved = resolveClientFacingBaseURL(
    env.NEXT_PUBLIC_LANGGRAPH_BASE_URL,
  );
  if (resolved) {
    return `${resolved}/api/langgraph`;
  } else if (isMock) {
    if (typeof window !== "undefined") {
      return `${window.location.origin}/mock/api`;
    }
    return "http://localhost:3000/mock/api";
  } else {
    // LangGraph SDK requires a full URL, construct it from current origin
    if (typeof window !== "undefined") {
      return `${window.location.origin}/api/langgraph`;
    }
    // Fallback for SSR
    return "http://localhost:2026/api/langgraph";
  }
}
