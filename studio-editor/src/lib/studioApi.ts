export const STUDIO_MODE = import.meta.env.VITE_CLIP_STUDIO === '1';
const KEY_NAME = 'clip.studio.key.v1';

export function studioKey(): string {
  return localStorage.getItem(KEY_NAME) ?? '';
}

export function rememberStudioKey(key: string): void {
  localStorage.setItem(KEY_NAME, key);
}

export function forgetStudioKey(): void {
  localStorage.removeItem(KEY_NAME);
}

export async function studioRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const key = studioKey();
  if (key) headers.set('X-API-Key', key);
  const response = await fetch(path, { ...init, headers, credentials: 'same-origin' });
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try { message = (await response.json()).detail || message; } catch { /* No JSON error body. */ }
    throw new Error(message);
  }
  return response.json() as Promise<T>;
}

export async function uploadStudioFile<T>(path: string, file: File): Promise<T> {
  const body = new FormData();
  body.append('file', file);
  return studioRequest<T>(path, { method: 'POST', body });
}

export interface StudioAsset {
  id: string;
  name: string;
  kind: 'video' | 'audio' | 'image';
  url: string;
  source_clip_id?: string | null;
}

export async function uploadStudioAsset(file: File): Promise<{ source: string; url: string; persisted: true }> {
  const asset = await uploadStudioFile<StudioAsset>('/studio/assets/upload', file);
  return { source: asset.url, url: asset.url, persisted: true };
}
