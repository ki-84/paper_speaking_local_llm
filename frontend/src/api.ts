export type Row = {
  id: string;
  data: Record<string, any>;
  state: string;
  [key: string]: any;
};
export async function api<T = any>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...init,
    headers: {
      ...(init.body && !(init.body instanceof FormData)
        ? { "Content-Type": "application/json" }
        : {}),
      ...init.headers,
    },
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    if (response.status === 401) window.dispatchEvent(new Event("signed-out"));
    throw new Error(
      typeof data.detail === "string"
        ? data.detail
        : "This request did not work. Please try again.",
    );
  }
  return response.json();
}
export const post = (path: string, data?: unknown) =>
  api(path, {
    method: "POST",
    body: data === undefined ? undefined : JSON.stringify(data),
  });
export const fileUrl = (path: string) =>
  `/api/files/${path.split("/").map(encodeURIComponent).join("/")}`;
export const date = (value: number | string) =>
  new Date(typeof value === "number" ? value * 1000 : value).toLocaleDateString(
    "en-US",
    { month: "short", day: "numeric" },
  );
export const minutes = (seconds: number) =>
  `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;

// Keep an interrupted upload on this browser until the server has saved it.
function recordingDB(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const r = indexedDB.open("paperspeak-recording", 1);
    r.onupgradeneeded = () => r.result.createObjectStore("pending");
    r.onsuccess = () => resolve(r.result);
    r.onerror = () => reject(r.error);
  });
}
export async function pendingRecording(
  action: "get" | "put" | "delete",
  value?: any,
): Promise<any> {
  const db = await recordingDB();
  return new Promise((resolve, reject) => {
    const t = db.transaction(
      "pending",
      action === "get" ? "readonly" : "readwrite",
    );
    const s = t.objectStore("pending");
    const r =
      action === "get"
        ? s.get("recording")
        : action === "put"
          ? s.put(value, "recording")
          : s.delete("recording");
    t.oncomplete = () => {
      resolve(r.result);
      db.close();
    };
    t.onerror = () => {
      reject(t.error);
      db.close();
    };
  });
}
