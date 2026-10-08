# -*- coding: utf-8 -*-
"""DSH (DeepSeek Harness) 本地 API 客户端。

桌宠通过本机正在运行的 DSH 服务(默认 http://127.0.0.1:3080)进行 AI 对话,
复用 DSH 已配置好的模型/密钥与会话,不需要额外运行时、不引入第三方依赖。

协议(见 DSH 仓库 packages/host/apiproxy 与 packages/client/connection):
  * 一元调用: POST /api/<method>,请求体是四象限信封
        {"type":"client-request","rpcId":"<uuid>","method":"<method>","payload":{...}}
    响应体 {"type":"server-response","rpcId":...,"result":{"ok":true,"value":{...}}}
    业务错误也是 HTTP 200,错误在 result.ok=false 的 error 分支里。
    载体层错误: 404 路径未知 / 415 Content-Type 非 application/json / 400 请求体非 JSON。
  * 事件流: WS /api/events.mux  —— 注意:普通 GET 会返回 426,
    网络上**没有 SSE 回退**,只有 in-process 载体才走 SSE,所以这里自己实现最小
    WebSocket 客户端。每个文本帧是一个 ServerRequest 信封:
        {"type":"server-request","rpcId":...,"method":"<帧类型>","payload":<帧>}
    该 socket 上客户端不发送业务数据(只需回 pong/close)。
  * 应答服务端请求: POST /api/respond,体为 {"type":"client-response",...}

仅使用标准库。
"""
import base64
import json
import os
import socket
import urllib.error
import urllib.request
import uuid
from urllib.parse import urlparse

DEFAULT_BASE_URL = "http://127.0.0.1:3080"
# 一元调用超时(秒)
RPC_TIMEOUT = 30
# 事件流没有任何帧的超时(秒)
WS_READ_TIMEOUT = 120


class DshError(Exception):
    """DSH 调用失败(网络不可达、业务错误等)。"""


def default_base_url():
    """DSH 服务地址:优先环境变量 DSH_WEB_URL,否则本地默认端口。"""
    return (os.environ.get("DSH_WEB_URL") or DEFAULT_BASE_URL).rstrip("/")


# ════════════════ 最小 WebSocket 客户端 ════════════════
class DshEventStream:
    """/api/events.mux 的只读 WebSocket 流:逐帧产出 ServerRequest 信封。"""

    def __init__(self, base_url, path="/api/events.mux", timeout=WS_READ_TIMEOUT):
        self._timeout = timeout
        self._sock = None
        self._buf = b""
        url = urlparse(base_url)
        host = url.hostname or "127.0.0.1"
        port = url.port or (443 if url.scheme == "https" else 80)
        if url.scheme == "https":
            import ssl
            raw = socket.create_connection((host, port), timeout=timeout)
            self._sock = ssl.create_default_context().wrap_socket(raw, server_hostname=host)
        else:
            self._sock = socket.create_connection((host, port), timeout=timeout)
        self._sock.settimeout(timeout)
        try:
            self._handshake(host, port, path)
        except Exception:
            self.close()
            raise

    # ── 握手 ──
    def _handshake(self, host, port, path):
        key = base64.b64encode(os.urandom(16)).decode()
        req = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n"
            f"\r\n"
        )
        self._sock.sendall(req.encode("ascii"))
        # 读到响应头结束
        header = b""
        while b"\r\n\r\n" not in header:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise DshError("DSH 事件流握手失败:连接被关闭")
            header += chunk
            if len(header) > 65536:
                raise DshError("DSH 事件流握手响应异常")
        head, _, rest = header.partition(b"\r\n\r\n")
        self._buf = rest
        status_line = head.split(b"\r\n", 1)[0].decode("latin-1", "replace")
        if " 101" not in status_line:
            raise DshError(f"DSH 事件流握手失败:{status_line}")

    # ── 帧收发 ──
    def _recv_exact(self, n):
        while len(self._buf) < n:
            chunk = self._sock.recv(max(4096, n - len(self._buf)))
            if not chunk:
                raise DshError("DSH 事件流已断开")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def _send_frame(self, opcode, payload=b""):
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        n = len(payload)
        header = bytes([0x80 | opcode])
        if n < 126:
            header += bytes([0x80 | n])
        elif n < 65536:
            header += bytes([0x80 | 126]) + n.to_bytes(2, "big")
        else:
            header += bytes([0x80 | 127]) + n.to_bytes(8, "big")
        self._sock.sendall(header + mask + masked)

    def _read_message(self):
        """读一条完整消息(处理分片/ping/close),返回文本或 None(表示流结束)。"""
        data = b""
        while True:
            b1, b2 = self._recv_exact(2)
            fin = b1 & 0x80
            opcode = b1 & 0x0F
            length = b2 & 0x7F
            if length == 126:
                length = int.from_bytes(self._recv_exact(2), "big")
            elif length == 127:
                length = int.from_bytes(self._recv_exact(8), "big")
            mask = self._recv_exact(4) if (b2 & 0x80) else None
            payload = self._recv_exact(length) if length else b""
            if mask:
                payload = bytes(x ^ mask[i % 4] for i, x in enumerate(payload))

            if opcode == 0x9:      # ping → pong
                try:
                    self._send_frame(0xA, payload)
                except Exception:
                    pass
                continue
            if opcode == 0xA:      # pong → 忽略
                continue
            if opcode == 0x8:      # close
                return None
            if opcode in (0x1, 0x0):   # text / continuation
                data += payload
                if fin:
                    return data.decode("utf-8", "replace")
                continue
            # 其他(二进制等)忽略整条
            if fin:
                continue

    def __iter__(self):
        while True:
            try:
                text = self._read_message()
            except socket.timeout:
                raise DshError(f"DSH 事件流 {self._timeout}s 没有数据,已放弃")
            if text is None:
                return
            if not text:
                continue
            try:
                yield json.loads(text)
            except Exception:
                continue

    def close(self):
        if self._sock is not None:
            try:
                self._send_frame(0x8, b"")
            except Exception:
                pass
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None


# ════════════════ DSH API 客户端 ════════════════
class DshClient:
    """DSH /api 的最小客户端。"""

    def __init__(self, base_url=None, timeout=RPC_TIMEOUT, read_timeout=WS_READ_TIMEOUT):
        self.base_url = (base_url or default_base_url()).rstrip("/")
        self.timeout = timeout
        self.read_timeout = read_timeout

    # ── 一元调用 ──
    def _post(self, path, body, timeout=None):
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            self.base_url + path,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            raise DshError(f"DSH 返回 HTTP {e.code}({path})") from e
        except urllib.error.URLError as e:
            raise DshError(
                f"连不上 DSH({self.base_url}),请确认 DSH 正在运行。{e.reason}"
            ) from e
        except Exception as e:
            raise DshError(f"请求 DSH 失败: {e}") from e
        try:
            return json.loads(raw)
        except Exception as e:
            raise DshError(f"DSH 响应不是 JSON: {raw[:200]}") from e

    def rpc(self, method, payload=None):
        """调用一个一元方法,成功返回 result.value,业务错误抛 DshError。"""
        body = {
            "type": "client-request",
            "rpcId": str(uuid.uuid4()),
            "method": method,
            "payload": payload if payload is not None else {},
        }
        data = self._post("/api/" + method, body)
        result = data.get("result") or {}
        if not result.get("ok"):
            err = result.get("error") or {}
            raise DshError(f"{method} 失败: {err.get('message') or err.get('code') or '未知错误'}")
        return result.get("value")

    def respond(self, rpc_id, value):
        """应答一个服务端请求(授权/提问),value 是它的 result.value。"""
        body = {
            "type": "client-response",
            "rpcId": rpc_id,
            "result": {"ok": True, "value": value},
        }
        return self._post("/api/respond", body)

    # ── 会话 ──
    def create_session(self, cwd=None):
        payload = {}
        if cwd:
            payload["cwd"] = cwd
        value = self.rpc("session.create", payload) or {}
        sid = value.get("sessionId")
        if not sid:
            raise DshError("session.create 没有返回 sessionId")
        return sid

    def prompt(self, session_id, text, images=None):
        """发送一条消息。images 是可选的 [(mediaType, base64)] 列表。"""
        content = []
        for media_type, b64 in (images or []):
            content.append({"type": "image", "mediaType": media_type, "data": b64})
        content.append({"type": "text", "text": text})
        return self.rpc("session.prompt", {
            "sessionId": session_id,
            "mode": "queue",
            "content": content,
        })

    def cancel(self, session_id):
        try:
            return self.rpc("session.cancel", {"sessionId": session_id})
        except DshError:
            return None

    # ── 事件流 ──
    def open_mux(self):
        """打开事件流(WebSocket)。返回可迭代对象,调用方负责 close。"""
        try:
            return DshEventStream(self.base_url, "/api/events.mux", self.read_timeout)
        except DshError:
            raise
        except Exception as e:
            raise DshError(f"打开 DSH 事件流失败: {e}") from e
