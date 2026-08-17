import { request } from '@/utils/request';

interface LoginPublicKey {
  algorithm: 'RSA-OAEP-256';
  public_key: string;
  key_id: string;
  expires_in: number;
}

function pemToBuffer(pem: string): ArrayBuffer {
  const base64 = pem.replace(/-----BEGIN PUBLIC KEY-----|-----END PUBLIC KEY-----|\s/g, '');
  const bytes = Uint8Array.from(atob(base64), char => char.charCodeAt(0));
  return bytes.buffer;
}

function toBase64(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let binary = '';
  bytes.forEach(byte => { binary += String.fromCharCode(byte); });
  return btoa(binary);
}

export async function encryptLoginCredentials(username: string, password: string) {
  if (!window.crypto?.subtle) {
    throw new Error('当前浏览器不支持安全登录加密，请升级浏览器。');
  }

  const response = await request<LoginPublicKey>({
    url: '/auth/login-key/',
    method: 'GET',
  });
  if (!response.success || !response.data) {
    throw new Error(response.error || '无法获取登录加密密钥。');
  }

  const publicKey = await window.crypto.subtle.importKey(
    'spki',
    pemToBuffer(response.data.public_key),
    { name: 'RSA-OAEP', hash: 'SHA-256' },
    false,
    ['encrypt'],
  );
  const nonceBytes = window.crypto.getRandomValues(new Uint8Array(16));
  const payload = JSON.stringify({
    username,
    password,
    timestamp: Date.now(),
    nonce: Array.from(nonceBytes, byte => byte.toString(16).padStart(2, '0')).join(''),
  });
  const encrypted = await window.crypto.subtle.encrypt(
    { name: 'RSA-OAEP' },
    publicKey,
    new TextEncoder().encode(payload),
  );

  return { encrypted_payload: toBase64(encrypted), key_id: response.data.key_id };
}
