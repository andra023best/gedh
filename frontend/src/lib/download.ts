// Завантаження захищеного файлу: запит із токеном, потім файл віддається браузеру.
import { apiRaw } from './session';

export async function blobFetch(path: string): Promise<void> {
  const res = await apiRaw(path);
  const name = /filename="([^"]+)"/.exec(res.headers.get('content-disposition') ?? '')?.[1] ?? 'file';
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}
