import { fetchBackend } from "../backend/fetch";

import type { Model } from "./types";

export async function loadModels() {
  const res = await fetchBackend("/api/models");
  const { models } = (await res.json()) as { models: Model[] };
  return models;
}
