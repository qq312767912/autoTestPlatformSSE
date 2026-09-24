import socket
import ssl
import time
import urllib.error
import urllib.request

from celery import shared_task
from django.utils import timezone

from .models import TestHostDiagnosis
from .validators import validate_safe_ipv4


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _tcp_probe(ip, port):
    started = time.monotonic()
    try:
        with socket.create_connection((ip, port), timeout=5):
            return {"ok": True, "port": port, "duration_ms": int((time.monotonic() - started) * 1000)}
    except OSError as exc:
        return {"ok": False, "port": port, "duration_ms": int((time.monotonic() - started) * 1000), "error": str(exc)[:200]}


def _http_probe(hostname, ip, scheme):
    port = 443 if scheme == "https" else 80
    request = urllib.request.Request(f"{scheme}://{hostname}/", method="HEAD", headers={"User-Agent": "WHartTest-Host-Diagnosis/1.0"})
    opener = urllib.request.build_opener(NoRedirectHandler)
    started = time.monotonic()
    original_getaddrinfo = socket.getaddrinfo

    def pinned_getaddrinfo(host, requested_port, *args, **kwargs):
        if host == hostname:
            return original_getaddrinfo(ip, requested_port, *args, **kwargs)
        return original_getaddrinfo(host, requested_port, *args, **kwargs)

    socket.getaddrinfo = pinned_getaddrinfo
    try:
        context = ssl.create_default_context() if scheme == "https" else None
        if context:
            opener = urllib.request.build_opener(NoRedirectHandler, urllib.request.HTTPSHandler(context=context))
        with opener.open(request, timeout=5) as response:
            return {"ok": True, "scheme": scheme, "port": port, "status": response.status, "duration_ms": int((time.monotonic() - started) * 1000)}
    except urllib.error.HTTPError as exc:
        return {"ok": 300 <= exc.code < 400, "scheme": scheme, "port": port, "status": exc.code, "duration_ms": int((time.monotonic() - started) * 1000), "error": str(exc)[:200]}
    except (urllib.error.URLError, ssl.SSLError, OSError) as exc:
        return {"ok": False, "scheme": scheme, "port": port, "duration_ms": int((time.monotonic() - started) * 1000), "error": str(exc)[:200]}
    finally:
        socket.getaddrinfo = original_getaddrinfo


@shared_task(name="test_host_config.tasks.run_host_diagnosis", soft_time_limit=20, time_limit=25)
def run_host_diagnosis(diagnosis_id):
    diagnosis = TestHostDiagnosis.objects.select_related("mapping").get(pk=diagnosis_id)
    diagnosis.status = "running"
    diagnosis.started_at = timezone.now()
    diagnosis.save(update_fields=["status", "started_at"])
    started = time.monotonic()
    try:
        mapping = diagnosis.mapping
        expected_ip = validate_safe_ipv4(mapping.ipv4)
        resolved = sorted({entry[4][0] for entry in socket.getaddrinfo(mapping.hostname, None, socket.AF_INET)})
        dns_ok = expected_ip in resolved
        tcp = [_tcp_probe(expected_ip, 80), _tcp_probe(expected_ip, 443)]
        http = [_http_probe(mapping.hostname, expected_ip, "http"), _http_probe(mapping.hostname, expected_ip, "https")]
        result = {
            "format": {"ok": True},
            "dns": {"ok": dns_ok, "expected": expected_ip, "resolved": resolved},
            "tcp": tcp,
            "http": http,
        }
        ok = dns_ok and any(item["ok"] for item in tcp) and any(item["ok"] for item in http)
        diagnosis.status = "success" if ok else "failed"
        if not dns_ok:
            diagnosis.error_code = "DNS_MISMATCH" if resolved else "DNS_NOT_RESOLVED"
            diagnosis.error_message = "DNS 解析结果与配置 IP 不一致"
        elif not any(item["ok"] for item in tcp):
            diagnosis.error_code = "PORT_UNREACHABLE"
            diagnosis.error_message = "80 和 443 端口均不可达"
        elif not any(item["ok"] for item in http):
            diagnosis.error_code = "HTTP_UNAVAILABLE"
            diagnosis.error_message = "HTTP/HTTPS 访问失败"
        diagnosis.result = result
    except socket.gaierror as exc:
        diagnosis.status = "failed"
        diagnosis.error_code = "DNS_NOT_RESOLVED"
        diagnosis.error_message = str(exc)[:500]
        diagnosis.result = {"format": {"ok": True}, "dns": {"ok": False, "resolved": []}}
    except Exception as exc:
        diagnosis.status = "failed"
        diagnosis.error_code = "DIAGNOSIS_ERROR"
        diagnosis.error_message = str(exc)[:500]
    diagnosis.duration_ms = int((time.monotonic() - started) * 1000)
    diagnosis.finished_at = timezone.now()
    diagnosis.save(update_fields=["status", "result", "error_code", "error_message", "duration_ms", "finished_at"])
    return diagnosis.status
