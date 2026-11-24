"""
PDF 文本提取（纯标准库）。

只为一件事服务：把巨潮的公告 PDF 变成可检索、可引用的文字。
做法是解析 ToUnicode CMap，把 CID 字形码还原成 Unicode——
中文 PDF 基本都是这种结构，不做这一步就只能拿到一堆 <0014> 这样的乱码。

限制（必须如实告知，不能让上层误以为都能读）：
  · 纯扫描件（无文本层）读不出，会返回空串，上层标记"需人工查看"；
  · 复杂排版（多栏、表格）只能按内容流顺序输出，不保证阅读顺序；
  · 只取前若干内容流，超长文件会截断。
"""
from __future__ import annotations

import re
import zlib

_OBJ_RE = re.compile(rb"(\d+)\s+0\s+obj\b(.*?)endobj", re.S)
_STREAM_RE = re.compile(rb"stream\r?\n(.*?)\r?\n?endstream", re.S)
_BFCHAR = re.compile(rb"beginbfchar(.*?)endbfchar", re.S)
_BFRANGE = re.compile(rb"beginbfrange(.*?)endbfrange", re.S)
_PAIR = re.compile(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>")
_RANGE = re.compile(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(<([0-9A-Fa-f]+)>|\[(.*?)\])", re.S)


def _hex_to_text(h: bytes) -> str:
    try:
        return bytes.fromhex(h.decode("ascii", "ignore")).decode("utf-16-be", "ignore")
    except Exception:
        return ""


def _inflate(raw: bytes) -> bytes | None:
    for wbits in (zlib.MAX_WBITS, -zlib.MAX_WBITS, 15 + 32):
        try:
            return zlib.decompress(raw, wbits)
        except Exception:
            continue
    return None


def _objects(data: bytes) -> dict[int, bytes]:
    return {int(m.group(1)): m.group(2) for m in _OBJ_RE.finditer(data)}


def _stream_body(obj: bytes) -> bytes | None:
    m = _STREAM_RE.search(obj)
    if not m:
        return None
    raw = m.group(1)
    return _inflate(raw) if b"FlateDecode" in obj else raw


def _parse_cmap(body: bytes) -> tuple[dict[int, str], int]:
    """返回 (码→文字, 码宽)。码宽 2 表示 CID 双字节。"""
    cmap: dict[int, str] = {}
    width = 1
    for blk in _BFCHAR.findall(body):
        for src, dst in _PAIR.findall(blk):
            code = int(src, 16)
            width = max(width, 2 if len(src) > 2 else 1)
            cmap[code] = _hex_to_text(dst)
    for blk in _BFRANGE.findall(body):
        for m in _RANGE.finditer(blk):
            lo, hi = int(m.group(1), 16), int(m.group(2), 16)
            width = max(width, 2 if len(m.group(1)) > 2 else 1)
            n = hi - lo
            if n < 0 or n > 65535:
                continue
            if m.group(4):  # <dst> 递进
                base = bytes.fromhex(m.group(4).decode()).decode("utf-16-be", "ignore")
                if not base:
                    continue
                head, last = base[:-1], ord(base[-1])
                for off in range(n + 1):
                    cmap[lo + off] = head + chr(last + off)
            elif m.group(5) is not None:
                items = re.findall(rb"<([0-9A-Fa-f]+)>", m.group(5))
                for off, it in enumerate(items[:n + 1]):
                    cmap[lo + off] = _hex_to_text(it)
    return cmap, width


def build_font_maps(data: bytes) -> tuple[dict[str, tuple[dict[int, str], int]], dict[int, str]]:
    """
    返回 ({字体资源名: (映射, 码宽)}, 合并映射)。
    资源名来自 /Font << /FT8 12 0 R >>，与内容流里的 /FT8 .. Tf 对应。
    """
    objs = _objects(data)
    # 对象号 → 映射
    obj_map: dict[int, tuple[dict[int, str], int]] = {}
    for num, obj in objs.items():
        m = re.search(rb"/ToUnicode\s+(\d+)\s+0\s+R", obj)
        if not m:
            continue
        tgt = int(m.group(1))
        target = objs.get(tgt)
        if target is None:
            continue
        body = _stream_body(target)
        if not body:
            continue
        cmap, width = _parse_cmap(body)
        if cmap:
            obj_map[num] = (cmap, width)

    # 资源名 → 映射
    named: dict[str, tuple[dict[int, str], int]] = {}
    for obj in objs.values():
        for fm in re.finditer(rb"/Font\s*<<(.{0,4000}?)>>", obj, re.S):
            for nm, onum in re.findall(rb"/([#\w\-\+\.]+)\s+(\d+)\s+0\s+R", fm.group(1)):
                oi = int(onum)
                if oi in obj_map:
                    named[nm.decode("latin-1")] = obj_map[oi]

    merged: dict[int, str] = {}
    for cm, _w in obj_map.values():
        merged.update(cm)
    return named, merged


def _decode_codes(raw: bytes, cmap: dict[int, str] | None, width: int) -> str:
    out = []
    step = 2 if width == 2 else 1
    for i in range(0, len(raw) - step + 1, step):
        code = int.from_bytes(raw[i:i + step], "big")
        if cmap and code in cmap:
            out.append(cmap[code])
        elif not cmap and 0x4E00 <= code <= 0x9FFF:
            out.append(chr(code))
        elif not cmap and 32 <= code < 127:
            out.append(chr(code))
    return "".join(out)


_TOKEN = re.compile(rb"<([0-9A-Fa-f\s]+)>\s*Tj|\((?:[^()\\]|\\.)*\)\s*Tj|\[(.*?)\]\s*TJ|/([#\w\-\+\.]+)\s+[\d.]+\s+Tf|\bET\b|\bTd\b|\bTD\b|\bT\*", re.S)


def extract_text(data: bytes, max_chars: int = 24000) -> str:
    """尽力提取 PDF 文字。失败返回空串。"""
    if not data[:5] == b"%PDF-" and b"%PDF-" not in data[:1024]:
        return ""
    named, merged = build_font_maps(data)
    objs = _objects(data)

    # 内容流：解压后含文本操作符的对象，按对象号顺序
    streams: list[bytes] = []
    for num in sorted(objs):
        if b"/ToUnicode" in objs[num]:
            continue
        body = _stream_body(objs[num])
        if not body:
            continue
        if b"Tj" in body or b"TJ" in body:
            streams.append(body)
        if len(streams) >= 60:
            break

    chunks: list[str] = []
    total = 0
    for body in streams:
        cur: tuple[dict[int, str] | None, int] = (merged or None, 2 if merged else 1)
        for m in _TOKEN.finditer(body):
            if m.group(3):
                nm = m.group(3).decode("latin-1")
                cur = named.get(nm, cur)
            elif m.group(1) is not None:
                h = re.sub(rb"\s", b"", m.group(1))
                if len(h) % 2:
                    h += b"0"
                chunks.append(_decode_codes(bytes.fromhex(h.decode()), cur[0], cur[1]))
            elif m.group(2) is not None:
                for hm in re.finditer(rb"<([0-9A-Fa-f\s]+)>", m.group(2)):
                    h = re.sub(rb"\s", b"", hm.group(1))
                    if len(h) % 2:
                        h += b"0"
                    chunks.append(_decode_codes(bytes.fromhex(h.decode()), cur[0], cur[1]))
                chunks.append("")
            elif m.group(0).strip() in (b"Td", b"TD", b"T*"):
                chunks.append(" ")
            elif m.group(0).strip() == b"ET":
                chunks.append("\n")
        chunks.append("\n")
        total = sum(len(c) for c in chunks)
        if total > max_chars:
            break

    text = "".join(chunks)
    text = text.replace("\x00", "")
    lines = []
    for ln in text.split("\n"):
        ln = _tidy(ln)
        if ln:
            lines.append(ln)
    return "\n".join(_merge_fragments(lines))[:max_chars]


_CJK_NEI = re.compile(r"(?<=[\u4e00-\u9fff：；，。、（）《》“”])[ ]+(?=[\u4e00-\u9fff：；，。、（）《》“”0-9A-Za-z])")
_DIGIT_NEI = re.compile(r"(?<=\d)[ ]+(?=[\d.%])")


_SENT_END = re.compile(r"[。！？；：!?;:]$")


def _merge_fragments(lines: list[str]) -> list[str]:
    """
    PDF 逐字定位会把一句话拆成多行（甚至每行几个字）。
    这里按标点把碎片拼回句子，否则证据句永远是"（二）公司以集中竞价交易方式回购股份，符合下列要求："这种半句。
    """
    out: list[str] = []
    buf = ""
    for ln in lines:
        buf = (buf + ln) if buf else ln
        if _SENT_END.search(buf) or len(buf) >= 130:
            out.append(buf)
            buf = ""
    if buf:
        out.append(buf)
    return out


def _tidy(line: str) -> str:
    """PDF 逐字定位会把中文拆成"证 券 代 码"，这里合回去。"""
    s = re.sub(r"[ \t]{2,}", " ", line).strip()
    s = _CJK_NEI.sub("", s)
    s = _DIGIT_NEI.sub("", s)
    return s


def looks_like_text(text: str, min_cjk: int = 60) -> bool:
    n = len(re.findall(r"[\u4e00-\u9fff]", text or ""))
    return n >= min_cjk
