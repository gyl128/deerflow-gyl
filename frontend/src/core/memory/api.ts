import { fetchBackend } from "../backend/fetch";

import type { UserMemory } from "./types";

export async function loadMemory() {
  const memory = await fetchBackend("/api/memory");
  const json = await memory.json();
  return json as UserMemory;
}
