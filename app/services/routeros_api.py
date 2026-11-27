"""Minimal RouterOS API client, used to end a hotspot session on the router directly.

The RADIUS disconnect (CoA) only works when RADIUS still has an open session record. This
client talks to the router itself, so it can end a session that RADIUS no longer knows about.

It implements only what the app needs: log in, find active hotspot sessions for a MAC, and
remove them. The router user should have API access and nothing else.

Protocol: each word is length-prefixed; a sentence ends with an empty word. Replies are
!re (data), !done (end), !trap (error).
"""

import socket

from app.utils.mac import normalise_mac


class RouterApiError(Exception):
    pass


def encode_length(n: int) -> bytes:
    if n < 0x80:
        return bytes([n])
    if n < 0x4000:
        return (n | 0x8000).to_bytes(2, "big")
    if n < 0x200000:
        return (n | 0xC00000).to_bytes(3, "big")
    if n < 0x10000000:
        return (n | 0xE0000000).to_bytes(4, "big")
    return bytes([0xF0]) + n.to_bytes(4, "big")


def encode_word(word: str) -> bytes:
    data = word.encode()
    return encode_length(len(data)) + data


class RouterApi:
    def __init__(self, sock: socket.socket):
        self.sock = sock

    @classmethod
    def connect(cls, host: str, port: int = 8728, timeout: float = 5.0) -> "RouterApi":
        return cls(socket.create_connection((host, port), timeout=timeout))

    def close(self) -> None:
        self.sock.close()

    def __enter__(self) -> "RouterApi":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ----- wire format -----

    def _read_exact(self, n: int) -> bytes:
        data = b""
        while len(data) < n:
            chunk = self.sock.recv(n - len(data))
            if not chunk:
                raise RouterApiError("router closed the connection")
            data += chunk
        return data

    def _read_length(self) -> int:
        first = self._read_exact(1)[0]
        if first & 0x80 == 0:
            return first
        if first & 0xC0 == 0x80:
            return ((first & 0x3F) << 8) | self._read_exact(1)[0]
        if first & 0xE0 == 0xC0:
            rest = self._read_exact(2)
            return ((first & 0x1F) << 16) | (rest[0] << 8) | rest[1]
        if first & 0xF0 == 0xE0:
            rest = self._read_exact(3)
            return ((first & 0x0F) << 24) | (rest[0] << 16) | (rest[1] << 8) | rest[2]
        return int.from_bytes(self._read_exact(4), "big")

    def _read_sentence(self) -> list[str]:
        words = []
        while True:
            length = self._read_length()
            if length == 0:
                return words
            words.append(self._read_exact(length).decode(errors="replace"))

    def _talk(self, words: list[str]) -> list[list[str]]:
        """Send one command and return its reply sentences, up to and including !done."""
        self.sock.sendall(b"".join(encode_word(w) for w in words) + b"\x00")
        replies = []
        while True:
            sentence = self._read_sentence()
            if not sentence:
                continue
            if sentence[0] == "!trap":
                message = next((w[len("=message="):] for w in sentence if w.startswith("=message=")), "")
                raise RouterApiError(message or "router returned an error")
            replies.append(sentence)
            if sentence[0] == "!done":
                return replies

    # ----- commands -----

    def login(self, username: str, password: str) -> None:
        self._talk(["/login", f"=name={username}", f"=password={password}"])

    def hotspot_session_ids(self, mac: str) -> list[str]:
        replies = self._talk(
            ["/ip/hotspot/active/print", f"?mac-address={normalise_mac(mac)}", "=.proplist=.id"]
        )
        ids = []
        for sentence in replies:
            if sentence[0] == "!re":
                ids += [w[len("=.id="):] for w in sentence if w.startswith("=.id=")]
        return ids

    def remove_hotspot_session(self, session_id: str) -> None:
        self._talk(["/ip/hotspot/active/remove", f"=.id={session_id}"])


def end_hotspot_sessions(host: str, port: int, username: str, password: str, mac: str) -> int:
    """Remove every active hotspot session for this MAC. Returns how many were removed."""
    with RouterApi.connect(host, port) as api:
        api.login(username, password)
        ids = api.hotspot_session_ids(mac)
        for session_id in ids:
            api.remove_hotspot_session(session_id)
    return len(ids)
