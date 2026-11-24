"""
HTTP 客户端：仅用标准库。负责重试、超时、编码嗅探、同域限速、gzip 解压。

设计取向与需求文档一致——把"采集失败"当常态：
任何网络异常都收敛成 FetchError，由上层决定降级（跳过 / 用摘要 / 记入异常清单），
绝不让一次请求失败打断整批任务。
"""
from __future__ import annotations

import gzip
import io
import random
import re
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from dataclasses import dataclass

from ..config import CFG

UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
]

_ctx = ssl.create_default_context()
_ctx.check_hostname = False
_ctx.verify_mode = ssl.CERT_NONE

_lock = threading.Lock()
_last_hit: dict[str, float] = {}


class FetchError(Exception):
    def __init__(self, url: str, reason: str, status: int | None = None):
        super().__init__(f"{reason} @ {url[:120]}")
        self.url = url
        self.reason = reason
        self.status = status


@dataclass
class Response:
    url: str
    status: int
    headers: dict
    content: bytes
    text: str

    def json(self):
        import json
        return json.loads(self.text)


def _throttle(host: str) -> None:
    with _lock:
        last = _last_hit.get(host, 0.0)
        wait = CFG.host_interval - (time.time() - last)
        if wait > 0:
            time.sleep(wait + random.uniform(0, 0.15))
        _last_hit[host] = time.time()


def _decode(body: bytes, content_type: str) -> str:
    if body[:3] == b"\xef\xbb\xbf":
        return body[3:].decode("utf-8", "ignore")
    m = re.search(r"charset=([\w\-]+)", content_type or "", re.I)
    if m:
        enc = m.group(1).lower()
        try:
            return body.decode(enc, "ignore")
        except Exception:
            pass
    head = body[:4096].decode("ascii", "ignore")
    m = re.search(r'charset=["\']?([\w\-]+)', head, re.I)
    if m:
        try:
            return body.decode(m.group(1).lower(), "ignore")
        except Exception:
            pass
    for enc in ("utf-8", "gb18030", "gbk", "big5"):
        try:
            return body.decode(enc)
        except Exception:
            continue
    return body.decode("utf-8", "ignore")


def fetch(url: str, *, data: bytes | None = None, headers: dict | None = None,
          timeout: float | None = None, retries: int | None = None,
          referer: str | None = None) -> Response:
    timeout = timeout or CFG.timeout
    retries = CFG.retries if retries is None else retries
    last_err = "unknown"

    for attempt in range(retries + 1):
        host = urllib.parse.urlsplit(url).netloc
        _throttle(host)
        h = {
            "User-Agent": random.choice(UAS),
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "close",
        }
        if referer:
            h["Referer"] = referer
        if headers:
            h.update(headers)
        try:
            req = urllib.request.Request(url, data=data, headers=h)
            with urllib.request.urlopen(req, timeout=timeout, context=_ctx) as r:
                body = r.read()
                enc = (r.headers.get("Content-Encoding") or "").lower()
                if "gzip" in enc:
                    try:
                        body = gzip.decompress(body)
                    except Exception:
                        try:
                            body = gzip.GzipFile(fileobj=io.BytesIO(body)).read()
                        except Exception:
                            pass
                elif "deflate" in enc:
                    try:
                        body = zlib.decompress(body)
                    except Exception:
                        try:
                            body = zlib.decompress(body, -zlib.MAX_WBITS)
                        except Exception:
                            pass
                ctype = r.headers.get("Content-Type", "")
                text = ""
                if "text" in ctype or "json" in ctype or "xml" in ctype or not ctype:
                    text = _decode(body, ctype)
                return Response(url=r.geturl(), status=r.status,
                                headers=dict(r.headers), content=body, text=text)
        except urllib.error.HTTPError as e:
            last_err = f"HTTP {e.code}"
            if e.code in (403, 404, 410, 451):
                raise FetchError(url, last_err, e.code)
        except Exception as e:
            last_err = f"{type(e).__name__}: {str(e)[:80]}"

        if attempt < retries:
            time.sleep(0.6 * (attempt + 1) + random.uniform(0, 0.4))

    raise FetchError(url, last_err)


def post_form(url: str, fields: dict, **kw) -> str:
    data = urllib.parse.urlencode(fields).encode()
    h = {"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"}
    h.update(kw.pop("headers", {}) or {})
    return fetch(url, data=data, headers=h, **kw).text


def post_json(url: str, payload: dict, **kw) -> str:
    import json
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    h = {"Content-Type": "application/json; charset=UTF-8"}
    h.update(kw.pop("headers", {}) or {})
    return fetch(url, data=data, headers=h, **kw).text
