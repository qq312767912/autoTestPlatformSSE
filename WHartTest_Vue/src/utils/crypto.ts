import JSEncrypt from 'jsencrypt';
import { request } from '@/utils/request';

interface LoginPublicKey {
  public_key: string;
}

let cachedPublicKey: string | null = null;
let cachedAt = 0;
const KEY_CACHE_TTL = 10 * 60 * 1000;

async function fetchPublicKey(): Promise<string | null> {
  if (cachedPublicKey && Date.now() - cachedAt < KEY_CACHE_TTL) {
    return cachedPublicKey;
  }

  try {
    const response = await request<LoginPublicKey>({
      url: '/auth/login-key/',
      method: 'GET',
    });
    if (response.success && response.data?.public_key) {
      cachedPublicKey = response.data.public_key;
      cachedAt = Date.now();
      return cachedPublicKey;
    }
  } catch (error) {
    console.warn('获取登录公钥失败，将使用兼容登录方式。', error);
  }
  return null;
}

/**
 * 使用不依赖安全上下文的 JSEncrypt 加密密码。
 * 公钥不可用或加密失败时返回原密码，以兼容旧客户端和旧后端。
 */
export async function encryptPassword(password: string): Promise<string> {
  const publicKey = await fetchPublicKey();
  if (!publicKey) return password;

  try {
    const encryptor = new JSEncrypt();
    encryptor.setPublicKey(publicKey);
    const encrypted = encryptor.encrypt(password);
    if (encrypted) return encrypted;
  } catch (error) {
    console.warn('登录密码加密失败，将使用兼容登录方式。', error);
  }
  return password;
}
