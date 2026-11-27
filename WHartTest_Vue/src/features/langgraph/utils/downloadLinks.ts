import type { ToolFileAttachment } from './toolResultParser';

const decode = (value: string) => {
  try { return decodeURIComponent(value); } catch { return value; }
};
const basename = (value: string) => decode(value.split(/[?#]/)[0]).replace(/\\/g, '/').split('/').pop() || '';
const webUrl = (value: string) => /^(https?:\/\/|\/(?!\/))/i.test(value);

export function resolveDownloadLink(href: string, files: ToolFileAttachment[]): string | null {
  if (/^(javascript|data|vbscript):/i.test(href.trim())) return null;
  if (/^https?:\/\//i.test(href)) return href;
  const name = basename(href);
  const matches = files.filter(file => webUrl(file.url) &&
    (file.name === name || basename(file.path || file.url) === name));
  const urls = [...new Set(matches.map(file => file.url))];
  if (urls.length === 1) return urls[0];
  // Never guess between different runs with identically named artifacts.
  if (/^(sandbox:|file:)/i.test(href) || /^\/(mnt|tmp|app|workspace|data|home|Users)\//i.test(href)) return null;
  return webUrl(href) || href.startsWith('#') ? href : null;
}

export function repairDownloadLinks(html: string, files: ToolFileAttachment[]): string {
  const doc = new DOMParser().parseFromString(html, 'text/html');
  doc.querySelectorAll('a[href]').forEach(link => {
    const href = link.getAttribute('href') || '';
    const resolved = resolveDownloadLink(href, files);
    if (resolved) {
      link.setAttribute('href', resolved);
      link.setAttribute('rel', 'noopener noreferrer');
    } else {
      link.removeAttribute('href');
      link.setAttribute('title', '文件未登记或地址不可用，请从工具消息中的文件附件下载；若无附件，请重新生成文件。');
      link.textContent = `${link.textContent || '下载'}（文件不可用）`;
    }
  });
  return doc.body.innerHTML;
}
