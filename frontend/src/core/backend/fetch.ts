import { getBackendBaseURL } from "@/core/config";

function buildBackendCandidates(path: string) {
  const candidates: string[] = [];
  const push = (value?: string) => {
    if (!value || candidates.includes(value)) {
      return;
    }
    candidates.push(value);
  };

  if (typeof window !== "undefined") {
    push(new URL(path, window.location.origin).toString());
  }

  const configuredBaseURL = getBackendBaseURL();
  if (configuredBaseURL) {
    push(new URL(path, configuredBaseURL).toString());
  }

  push(path);
  return candidates;
}

function shouldRetryWithNextCandidate(
  response: Response,
  attemptIndex: number,
  candidateCount: number,
) {
  if (attemptIndex >= candidateCount - 1) {
    return false;
  }
  return (
    response.status === 404 ||
    response.status === 502 ||
    response.status === 503 ||
    response.status === 504
  );
}

export async function fetchBackend(path: string, init?: RequestInit) {
  const candidates = buildBackendCandidates(path);
  let lastError: unknown;
  let lastResponse: Response | undefined;

  for (const [index, candidate] of candidates.entries()) {
    try {
      const response = await fetch(candidate, init);
      if (
        response.ok ||
        !shouldRetryWithNextCandidate(response, index, candidates.length)
      ) {
        return response;
      }
      lastResponse = response;
    } catch (error) {
      lastError = error;
    }
  }

  if (lastResponse) {
    return lastResponse;
  }
  if (lastError instanceof Error) {
    throw lastError;
  }
  throw new Error("Failed to reach backend");
}
