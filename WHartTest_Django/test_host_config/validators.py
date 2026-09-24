import ipaddress
import re

from django.core.exceptions import ValidationError


HOSTNAME_PATTERN = re.compile(
    r"^(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)


def normalize_hostname(value: str) -> str:
    hostname = (value or "").strip().rstrip(".").lower()
    try:
        hostname = hostname.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValidationError("域名不符合 IDNA 规范") from exc
    if not HOSTNAME_PATTERN.fullmatch(hostname):
        raise ValidationError("请输入完整的精确域名，不得包含通配符、端口或路径")
    return hostname


def validate_safe_ipv4(value: str) -> str:
    try:
        address = ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError as exc:
        raise ValidationError("请输入有效的 IPv4 地址") from exc
    if (
        address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_unspecified
        or address == ipaddress.IPv4Address("255.255.255.255")
    ):
        raise ValidationError("不允许使用回环、链路本地、组播、未指定或广播地址")
    return str(address)
