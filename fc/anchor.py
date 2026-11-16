"""External anchoring (DESIGN.md §6.2).

A published Merkle root is anchored by **OpenTimestamps**, which produces a proof
that the root's digest existed before a given Bitcoin block. Nobody, including the
hub, can backdate it, so it is the only anchor allowed to carry an ordering claim.
Stamping through public calendars and verification against Bitcoin block headers are
live: the scheme is `fc-ots/v1` (the Merkle root, not the file hash, is stamped),
`python -m fc.anchor stamp|upgrade|verify` is the operator CLI, and verification
needs only committed headers (`roots/bitcoin-headers.json`) and no network.

Sigstore Rekor anchoring is deferred: nothing here writes or checks a Rekor entry,
and no claim in this repository depends on one.

Anchoring reaches third-party services, so the service core takes it behind an
injectable callable (`Anchor`): the unit suite never spawns a process or opens a
socket. `null_anchor` is the default and claims nothing.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import http.client
import json
import math
import os
import re
import struct
import sys
import tempfile
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import NamedTuple

from fc import __version__
from fc import commit as C

# Anchor status vocabulary. "failed" records a broken anchoring attempt without
# ever blocking receipt issuance.
NOT_ANCHORED = "not-anchored"
PENDING_CALENDAR = "pending-calendar"
BITCOIN = "bitcoin"
FAILED = "failed"
INVALID = "invalid"  # a proof or header that contradicts the claim it makes


class AnchorToolUnavailable(RuntimeError):
    """Raised when an anchoring operation needs the optional `ops` tooling."""


@dataclass
class AnchorResult:
    """Outcome of anchoring one root document."""

    status: str
    ots_path: str | None = None
    rekor_log_index: int | None = None
    rekor_bundle_path: str | None = None
    stamped_at: str | None = None
    calendar_uris: list[str] = field(default_factory=list)


# An anchor callable receives the published root document's path and its exact
# bytes, and returns what it managed to achieve.
Anchor = Callable[[Path, bytes], AnchorResult]


def null_anchor(path: Path, data: bytes) -> AnchorResult:
    """The default: publish the root, claim no external anchor.

    Used by the tracer and the whole unit suite so tests stay offline. It is
    honest rather than convenient — the root records `not-anchored`, and
    `fc verify` refuses to treat that as an anchored root.
    """
    return AnchorResult(status=NOT_ANCHORED)


OTS_MAGIC = b"\x00OpenTimestamps\x00\x00Proof\x00\xbf\x89\xe2\xe8\x84\xe8\x92\x94"
OTS_BITCOIN_TAG = b"\x05\x88\x96\x0d\x73\xd7\x19\x01"
OTS_PENDING_TAG = b"\x83\xdf\xe3\x0d\x2e\xf9\x0c\x8e"


# ===========================================================================
# Real OpenTimestamps verification (plan 06-03, FREEZE-03).
#
# A minimal, stdlib-only implementation of the .ots wire format and of Bitcoin
# block-header checking. It executes the operation chain from the digest to each
# attestation and compares the result with a block header's merkle root. There is
# no other admission path: a recorded status string or a claimed block time is
# never evidence.
# ===========================================================================

BITCOIN_TAG = OTS_BITCOIN_TAG
PENDING_TAG = OTS_PENDING_TAG
ANCHOR_SCHEME = "fc-ots/v1"

# Parser bounds (T-06-09): a proof is untrusted input that can arrive in a pull request.
MAX_PROOF_BYTES = 65536
MAX_DEPTH = 256
MAX_ARG_BYTES = 4096  # one op argument
MAX_MSG_BYTES = 8192  # any intermediate message an operation chain may produce
MAX_PAYLOAD_BYTES = 8192  # one attestation payload
MAX_URI_BYTES = 1000

# A header only counts as evidence if forging it would cost about 2^70 hashes
# (T-06-08). Read at call time so tests and demos can lower it explicitly.
MIN_HEADER_WORK_BITS = 70
# Miners may stamp a block up to two hours ahead of real time, so "anchored
# before" is the header time plus this margin, never the header time itself (T-06-11).
BLOCK_TIME_MARGIN_S = 7200

_OP_SHA1 = 0x02
_OP_RIPEMD160 = 0x03
_OP_SHA256 = 0x08
_OP_KECCAK256 = 0x67
_OP_APPEND = 0xF0
_OP_PREPEND = 0xF1
_OP_REVERSE = 0xF2
_OP_HEXLIFY = 0xF3
_UNARY_OPS = {_OP_SHA1, _OP_RIPEMD160, _OP_SHA256, _OP_KECCAK256, _OP_REVERSE, _OP_HEXLIFY}
_BINARY_OPS = {_OP_APPEND, _OP_PREPEND}
_OP_NAMES = {
    _OP_SHA1: "sha1", _OP_RIPEMD160: "ripemd160", _OP_SHA256: "sha256",
    _OP_KECCAK256: "keccak256", _OP_APPEND: "append", _OP_PREPEND: "prepend",
    _OP_REVERSE: "reverse", _OP_HEXLIFY: "hexlify",
}


class UnsupportedOp(Exception):
    """An operation this build cannot execute (ripemd160 on some OpenSSL 3 builds, keccak256)."""


# ---------------------------------------------------------------------------- bytes


def _write_varuint(n: int) -> bytes:
    if n < 0:
        raise ValueError("varuint cannot be negative")
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _write_varbytes(data: bytes) -> bytes:
    return _write_varuint(len(data)) + data


class _Reader:
    """Bounds-checked cursor over untrusted bytes; every failure is a ValueError."""

    def __init__(self, data: bytes, pos: int = 0) -> None:
        self.data = data
        self.pos = pos

    def read(self, n: int) -> bytes:
        if n < 0 or self.pos + n > len(self.data):
            raise ValueError("truncated OTS proof")
        chunk = self.data[self.pos : self.pos + n]
        self.pos += n
        return chunk

    def byte(self) -> int:
        return self.read(1)[0]

    def varuint(self) -> int:
        val = 0
        shift = 0
        while True:
            b = self.byte()
            val |= (b & 0x7F) << shift
            if not (b & 0x80):
                if b == 0 and shift > 0:
                    raise ValueError("non-canonical varuint in OTS proof")
                return val
            shift += 7
            if shift > 63:
                raise ValueError("varuint too long in OTS proof")

    def varbytes(self, limit: int) -> bytes:
        n = self.varuint()
        if n > limit:
            raise ValueError(f"OTS field of {n} bytes exceeds the {limit}-byte limit")
        return self.read(n)

    @property
    def done(self) -> bool:
        return self.pos == len(self.data)


def _sha256d(data: bytes) -> bytes:
    return hashlib.sha256(hashlib.sha256(data).digest()).digest()


def _ripemd160(data: bytes) -> bytes:
    try:
        return hashlib.new("ripemd160", data).digest()
    except ValueError as exc:  # OpenSSL 3 builds without the legacy provider
        raise UnsupportedOp("ripemd160 is not available in this Python/OpenSSL build") from exc


# ------------------------------------------------------------------------ operations


@dataclass(frozen=True, order=True)
class Op:
    """One step of the chain from the file digest to an attestation."""

    tag: int
    arg: bytes = b""

    @property
    def name(self) -> str:
        return _OP_NAMES.get(self.tag, f"0x{self.tag:02x}")

    def encode(self) -> bytes:
        if self.tag in _BINARY_OPS:
            return bytes([self.tag]) + _write_varbytes(self.arg)
        return bytes([self.tag])

    def apply(self, msg: bytes) -> bytes:
        if self.tag == _OP_SHA256:
            out = hashlib.sha256(msg).digest()
        elif self.tag == _OP_SHA1:
            out = hashlib.sha1(msg).digest()
        elif self.tag == _OP_RIPEMD160:
            out = _ripemd160(msg)
        elif self.tag == _OP_KECCAK256:
            raise UnsupportedOp("keccak256 is not supported")
        elif self.tag == _OP_APPEND:
            out = msg + self.arg
        elif self.tag == _OP_PREPEND:
            out = self.arg + msg
        elif self.tag == _OP_REVERSE:
            out = msg[::-1]
        elif self.tag == _OP_HEXLIFY:
            out = msg.hex().encode("ascii")
        else:  # pragma: no cover - parse rejects unknown tags
            raise ValueError(f"unknown OTS op 0x{self.tag:02x}")
        if len(out) > MAX_MSG_BYTES:
            raise ValueError(f"OTS operation result exceeds {MAX_MSG_BYTES} bytes")
        return out


# ---------------------------------------------------------------------- attestations


@dataclass(frozen=True)
class Bitcoin:
    """The message was committed in the Bitcoin block at this height."""

    height: int
    tag: bytes = field(default=BITCOIN_TAG, init=False, repr=False)

    def payload(self) -> bytes:
        return _write_varuint(self.height)


@dataclass(frozen=True)
class Pending:
    """A calendar server promised to include the message in a future Bitcoin block."""

    uri: str
    tag: bytes = field(default=PENDING_TAG, init=False, repr=False)

    def payload(self) -> bytes:
        return _write_varbytes(self.uri.encode("utf-8"))


@dataclass(frozen=True)
class Unknown:
    """An attestation type this verifier does not know; kept so a proof round-trips."""

    tag: bytes
    raw: bytes

    def payload(self) -> bytes:
        return self.raw


Attestation = Bitcoin | Pending | Unknown


def _encode_attestation(att: Attestation) -> bytes:
    return b"\x00" + att.tag + _write_varbytes(att.payload())


def _attestation_key(att: Attestation) -> tuple[bytes, bytes]:
    return (att.tag, att.payload())


def _parse_attestation(r: _Reader) -> Attestation:
    tag = r.read(8)
    payload = r.varbytes(MAX_PAYLOAD_BYTES)
    inner = _Reader(payload)
    if tag == BITCOIN_TAG:
        height = inner.varuint()
        if not inner.done:
            raise ValueError("trailing bytes in Bitcoin attestation")
        return Bitcoin(height)
    if tag == PENDING_TAG:
        uri_raw = inner.varbytes(MAX_URI_BYTES)
        if not inner.done:
            raise ValueError("trailing bytes in pending attestation")
        try:
            return Pending(uri_raw.decode("utf-8"))
        except UnicodeDecodeError as exc:
            raise ValueError("pending attestation URI is not valid UTF-8") from exc
    return Unknown(tag, payload)


# ----------------------------------------------------------------------- timestamps


@dataclass
class Timestamp:
    """A node of the proof tree: a message, what attests it, and ops leading onward.

    `msg` is None only when an operation on the path from the file digest could not
    be executed on this platform; `blocked` then names it. Such a node still
    parses and serializes (so a proof round-trips everywhere) but cannot be verified.
    """

    msg: bytes | None
    attestations: list[Attestation] = field(default_factory=list)
    ops: dict[Op, Timestamp] = field(default_factory=dict)
    blocked: str | None = None
    # Parse order, so serialize() reproduces the input byte for byte.
    _order: list[tuple[str, object]] = field(default_factory=list, repr=False, compare=False)

    # ------------------------------------------------------------------ building
    def add_attestation(self, att: Attestation) -> None:
        if att not in self.attestations:
            self.attestations.append(att)
            self._order.append(("a", att))

    def add_op(self, op: Op) -> Timestamp:
        """Return the child reached by `op`, creating it (and computing its message)."""
        child = self.ops.get(op)
        if child is None:
            child = self._child_for(op)
            self.ops[op] = child
            self._order.append(("o", op))
        return child

    def _child_for(self, op: Op) -> Timestamp:
        if self.msg is None:
            return Timestamp(None, blocked=self.blocked)
        try:
            return Timestamp(op.apply(self.msg))
        except UnsupportedOp as exc:
            return Timestamp(None, blocked=str(exc))

    def merge(self, other: Timestamp) -> None:
        """Fold another proof of the same message into this one."""
        if self.msg is not None and other.msg is not None and self.msg != other.msg:
            raise ValueError("cannot merge timestamps of different messages")
        for att in other.attestations:
            self.add_attestation(att)
        for op, child in other.ops.items():
            mine = self.ops.get(op)
            if mine is None:
                self.ops[op] = child
                self._order.append(("o", op))
            else:
                mine.merge(child)

    # ------------------------------------------------------------------- walking
    def walk_nodes(self) -> Iterator[Timestamp]:
        stack = [self]
        while stack:
            node = stack.pop()
            yield node
            stack.extend(reversed(list(node.ops.values())))

    def walk(self) -> Iterator[tuple[bytes | None, Attestation]]:
        """Yield (message, attestation) for every attestation in the tree."""
        for node in self.walk_nodes():
            for att in node.attestations:
                yield node.msg, att

    # ------------------------------------------------------------------- parsing
    @classmethod
    def parse(cls, r: _Reader, msg: bytes | None, depth: int = 0,
              blocked: str | None = None) -> Timestamp:
        if depth > MAX_DEPTH:
            raise ValueError(f"OTS proof nested deeper than {MAX_DEPTH} operations")
        ts = cls(msg, blocked=blocked)
        while True:
            tag = r.byte()
            more = tag == 0xFF
            if more:
                tag = r.byte()
            if tag == 0x00:
                att = _parse_attestation(r)
                ts.add_attestation(att)
            else:
                op = cls._parse_op(r, tag)
                child_template = ts._child_for(op)
                child = cls.parse(r, child_template.msg, depth + 1, child_template.blocked)
                existing = ts.ops.get(op)
                if existing is None:
                    ts.ops[op] = child
                    ts._order.append(("o", op))
                else:
                    existing.merge(child)
            if not more:
                return ts

    @staticmethod
    def _parse_op(r: _Reader, tag: int) -> Op:
        if tag in _BINARY_OPS:
            return Op(tag, r.varbytes(MAX_ARG_BYTES))
        if tag in _UNARY_OPS:
            return Op(tag)
        raise ValueError(f"unknown OTS operation 0x{tag:02x}")

    # ------------------------------------------------------------- serialization
    def _items(self) -> list[tuple[str, object]]:
        seen: set[tuple[str, object]] = set()
        items: list[tuple[str, object]] = []
        for kind, key in self._order:
            present = key in self.attestations if kind == "a" else key in self.ops
            if present and (kind, key) not in seen:
                seen.add((kind, key))
                items.append((kind, key))
        extra_a = sorted(
            (a for a in self.attestations if ("a", a) not in seen), key=_attestation_key)
        extra_o = sorted(op for op in self.ops if ("o", op) not in seen)
        return items + [("a", a) for a in extra_a] + [("o", o) for o in extra_o]

    def serialize(self, out: bytearray | None = None) -> bytes:
        buf = bytearray() if out is None else out
        items = self._items()
        if not items:
            raise ValueError("cannot serialize a timestamp with no attestations or operations")
        for i, (kind, key) in enumerate(items):
            if i < len(items) - 1:
                buf.append(0xFF)
            if kind == "a":
                buf += _encode_attestation(key)  # type: ignore[arg-type]
            else:
                buf += key.encode()  # type: ignore[attr-defined]
                self.ops[key].serialize(buf)  # type: ignore[index]
        return bytes(buf)


@dataclass
class DetachedProof:
    """A `.ots` file: the SHA-256 digest of the stamped file plus its proof tree."""

    digest: bytes
    timestamp: Timestamp


def parse_ots(data: bytes) -> DetachedProof:
    """Parse a detached OpenTimestamps proof (SHA-256 file digests only)."""
    if len(data) > MAX_PROOF_BYTES:
        raise ValueError(f"OTS proof of {len(data)} bytes exceeds the {MAX_PROOF_BYTES}-byte limit")
    if not data.startswith(OTS_MAGIC):
        raise ValueError("not an OpenTimestamps proof (bad header magic)")
    r = _Reader(data, len(OTS_MAGIC))
    version = r.varuint()
    if version != 1:
        raise ValueError(f"unsupported OTS version {version}")
    file_hash_op = r.byte()
    if file_hash_op != _OP_SHA256:
        raise ValueError(f"unsupported OTS file hash op 0x{file_hash_op:02x} (expected SHA-256)")
    digest = r.read(32)
    ts = Timestamp.parse(r, digest)
    if not r.done:
        raise ValueError("trailing bytes after the OTS proof tree")
    return DetachedProof(digest, ts)


def serialize_ots(proof: DetachedProof) -> bytes:
    if len(proof.digest) != 32:
        raise ValueError("OTS digest must be 32 bytes")
    return OTS_MAGIC + _write_varuint(1) + bytes([_OP_SHA256]) + proof.digest + (
        proof.timestamp.serialize())


# --------------------------------------------------------------------- block headers


@dataclass(frozen=True)
class BlockHeader:
    """An 80-byte Bitcoin block header."""

    raw: bytes

    @classmethod
    def from_bytes(cls, raw: bytes) -> BlockHeader:
        if len(raw) != 80:
            raise ValueError(f"a Bitcoin block header is 80 bytes, got {len(raw)}")
        return cls(bytes(raw))

    @property
    def version(self) -> int:
        return struct.unpack("<i", self.raw[0:4])[0]

    @property
    def prev_hash(self) -> bytes:
        return self.raw[4:36]

    @property
    def merkle_root(self) -> bytes:
        """Bytes 36..68 as stored — the value an OTS Bitcoin attestation's message equals."""
        return self.raw[36:68]

    @property
    def time(self) -> int:
        return struct.unpack("<I", self.raw[68:72])[0]

    @property
    def bits(self) -> int:
        return struct.unpack("<I", self.raw[72:76])[0]

    @property
    def nonce(self) -> int:
        return struct.unpack("<I", self.raw[76:80])[0]

    @property
    def block_hash_hex(self) -> str:
        return _sha256d(self.raw)[::-1].hex()

    @property
    def target(self) -> int:
        bits = self.bits
        exponent = bits >> 24
        mantissa = bits & 0x007FFFFF
        if bits & 0x00800000 or mantissa == 0:  # negative or zero targets are invalid
            return 0
        if exponent <= 3:
            return mantissa >> (8 * (3 - exponent))
        return mantissa << (8 * (exponent - 3))

    @property
    def work_bits(self) -> float:
        """log2 of the expected number of hashes needed to find a header this hard."""
        target = self.target
        if target <= 0 or target >= 1 << 256:
            return 0.0
        return 256.0 - math.log2(target + 1)

    @property
    def pow_ok(self) -> bool:
        target = self.target
        if target <= 0 or target >= 1 << 256:
            return False
        return int.from_bytes(_sha256d(self.raw), "little") <= target


class HeaderFile:
    """Block headers from a committed JSON file: {height: {"hash": ..., "header": ...}}.

    An entry is returned only when the header hashes to its stored hash and passes
    its own proof-of-work. A bare block time (the legacy bitcoin-headers.json format)
    is a claim, not evidence, and resolves to None.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._data: dict | None = None

    def _load(self) -> dict:
        if self._data is None:
            if not self.path.exists():
                self._data = {}
            else:
                loaded = json.loads(self.path.read_text())
                self._data = loaded if isinstance(loaded, dict) else {}
        return self._data

    def header(self, height: int) -> BlockHeader | None:
        entry = self._load().get(str(height))
        if not isinstance(entry, dict):
            return None
        try:
            hdr = BlockHeader.from_bytes(bytes.fromhex(entry["header"]))
        except (KeyError, TypeError, ValueError):
            return None
        stated = entry.get("hash")
        if not isinstance(stated, str) or stated.lower() != hdr.block_hash_hex:
            return None
        return hdr if hdr.pow_ok else None


# -------------------------------------------------------------------- verification


@dataclass
class AnchorVerdict:
    """What a proof establishes about one digest."""

    status: str
    height: int | None = None
    block_hash: str | None = None
    block_time: datetime | None = None
    anchored_before: datetime | None = None
    calendar_uris: list[str] = field(default_factory=list)
    detail: str = ""
    header_work_bits: float | None = None

    @property
    def confirmed(self) -> bool:
        return self.status == BITCOIN


def _digest_bytes(digest: bytes | str) -> bytes:
    if isinstance(digest, str):
        try:
            return bytes.fromhex(digest)
        except ValueError as exc:
            raise ValueError(f"digest is not hex: {digest!r}") from exc
    return bytes(digest)


def verify_digest_anchor(digest: bytes | str, proof: DetachedProof, headers) -> AnchorVerdict:
    """Decide whether `proof` shows `digest` was committed in a Bitcoin block.

    `headers` is any object with `header(height) -> BlockHeader | None` (HeaderFile,
    ExplorerHeaders, ChainedHeaders) or None. A Bitcoin attestation counts only when
    the header hashes correctly, carries at least MIN_HEADER_WORK_BITS of work, and
    the message the operation chain produces equals the header's merkle root.
    """
    want = _digest_bytes(digest)
    if proof.digest != want:
        return AnchorVerdict(
            INVALID,
            detail=f"proof is for digest {proof.digest.hex()}, not {want.hex()}",
        )

    floor = MIN_HEADER_WORK_BITS
    valid: list[tuple[datetime, int, BlockHeader]] = []
    uris: list[str] = []
    mismatched: list[int] = []
    weak: list[tuple[int, float]] = []
    missing: list[int] = []
    skipped: list[str] = []

    for node in proof.timestamp.walk_nodes():
        for att in node.attestations:
            if isinstance(att, Pending):
                if att.uri not in uris:
                    uris.append(att.uri)
            elif isinstance(att, Bitcoin):
                if node.msg is None:
                    skipped.append(node.blocked or "an unsupported operation")
                    continue
                hdr = headers.header(att.height) if headers is not None else None
                if hdr is None:
                    missing.append(att.height)
                elif hdr.work_bits < floor:
                    weak.append((att.height, hdr.work_bits))
                elif len(node.msg) != 32 or node.msg != hdr.merkle_root:
                    mismatched.append(att.height)
                else:
                    when = datetime.fromtimestamp(hdr.time, UTC)
                    valid.append((when, att.height, hdr))

    notes = ""
    if skipped:
        notes = "; skipped path(s) needing: " + ", ".join(sorted(set(skipped)))

    if valid:
        when, height, hdr = min(valid, key=lambda v: v[0])
        return AnchorVerdict(
            BITCOIN,
            height=height,
            block_hash=hdr.block_hash_hex,
            block_time=when,
            anchored_before=when + timedelta(seconds=BLOCK_TIME_MARGIN_S),
            calendar_uris=uris,
            detail=f"attested message equals block {height} merkle root{notes}",
            header_work_bits=hdr.work_bits,
        )
    if mismatched:
        h = mismatched[0]
        return AnchorVerdict(
            INVALID,
            height=h,
            calendar_uris=uris,
            detail=f"attested message does not match block {h} merkle root{notes}",
        )
    if weak:
        h, bits = weak[0]
        return AnchorVerdict(
            INVALID,
            height=h,
            calendar_uris=uris,
            detail=(f"header for block {h} carries only 2^{bits:.1f} work, below the "
                    f"2^{floor} floor{notes}"),
            header_work_bits=bits,
        )
    if missing:
        h = missing[0]
        return AnchorVerdict(
            PENDING_CALENDAR,
            height=h,
            calendar_uris=uris,
            detail=(f"header for block {h} not available "
                    f"(supply --bitcoin-headers or use --fetch){notes}"),
        )
    if uris:
        return AnchorVerdict(
            PENDING_CALENDAR,
            calendar_uris=uris,
            detail=f"calendar accepted the digest; no Bitcoin attestation yet{notes}",
        )
    if skipped:
        return AnchorVerdict(
            NOT_ANCHORED,
            detail="cannot evaluate the Bitcoin attestation: needs " + ", ".join(sorted(set(skipped))),
        )
    return AnchorVerdict(NOT_ANCHORED, detail="proof carries no attestation")


# ------------------------------------------------------------------------- networking


class _HttpsOnlyRedirects(urllib.request.HTTPRedirectHandler):
    """Refuse a redirect that would leave https."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.lower().startswith("https://"):
            raise urllib.error.URLError(f"refusing redirect to non-https URL {newurl!r}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def http_get(url: str, timeout: float = 20.0, max_bytes: int = 1024) -> bytes:
    """GET `url` over https and return at most `max_bytes` of the body.

    Anything larger raises ValueError rather than being truncated: a truncated
    header or hash must never look like a short valid one. Every network user in
    this module takes an injectable callable defaulting to this, so tests stay offline.
    """
    if not url.lower().startswith("https://"):
        raise ValueError(f"refusing non-https URL {url!r}")
    req = urllib.request.Request(url, headers={"User-Agent": f"future-challenge-fc/{__version__}"})
    opener = urllib.request.build_opener(_HttpsOnlyRedirects)
    with opener.open(req, timeout=timeout) as resp:
        body = resp.read(max_bytes + 1)
    if len(body) > max_bytes:
        raise ValueError(f"response from {url} exceeds {max_bytes} bytes")
    return body


# --------------------------------------------------------------------- header sources

BLOCKSTREAM_API = "https://blockstream.info/api"
MEMPOOL_API = "https://mempool.space/api"


class ExplorerHeaders:
    """Block headers from two public explorers that must agree (T-06-08).

    blockstream.info and mempool.space are asked for the block hash at a height and
    must return the same one. The header itself comes from blockstream.info and is
    returned only when it double-SHA-256 hashes to that agreed hash and passes its own
    proof-of-work. Any failure, disagreement or malformed answer resolves to None.
    """

    def __init__(self, http_get: Callable[[str, float, int], bytes] = http_get,
                 timeout: float = 20.0) -> None:
        self.http_get = http_get
        self.timeout = timeout

    def _hash_at(self, base: str, height: int) -> str | None:
        raw = self.http_get(f"{base}/block-height/{height}", self.timeout, 1024)
        text = raw.decode("ascii").strip().lower()
        if len(text) != 64 or any(c not in "0123456789abcdef" for c in text):
            return None
        return text

    def header(self, height: int) -> BlockHeader | None:
        try:
            first = self._hash_at(BLOCKSTREAM_API, height)
            second = self._hash_at(MEMPOOL_API, height)
            if first is None or first != second:
                return None
            raw = self.http_get(f"{BLOCKSTREAM_API}/block/{first}/header", self.timeout, 200)
            hdr = BlockHeader.from_bytes(bytes.fromhex(raw.decode("ascii").strip()))
        except (OSError, ValueError, http.client.HTTPException):
            return None
        if hdr.block_hash_hex != first or not hdr.pow_ok:
            return None
        return hdr


class ChainedHeaders:
    """Try each header source in order and return the first hit."""

    def __init__(self, *sources) -> None:
        self.sources = sources

    def header(self, height: int) -> BlockHeader | None:
        for source in self.sources:
            hdr = source.header(height)
            if hdr is not None:
                return hdr
        return None


# ----------------------------------------------------------------------------- regtest


def regtest_anchor(root_hex: str, block_time: datetime | int, height: int) -> tuple[bytes, str]:
    """Build a synthetic Bitcoin-attested proof and its block header.

    For tests and demos only. The header is mined at the easiest possible difficulty
    (bits 0x207fffff, about one bit of work), so production verification rejects it
    through MIN_HEADER_WORK_BITS; it verifies only when that floor is lowered
    explicitly. Returns (ots_bytes, header_hex).
    """
    digest = bytes.fromhex(root_hex)
    if len(digest) != 32:
        raise ValueError("root must be 32 bytes (64 hex characters)")
    ts = Timestamp(digest)
    node = ts.add_op(Op(_OP_SHA256))
    node.add_attestation(Bitcoin(height))
    assert node.msg is not None
    when = int(block_time.timestamp()) if isinstance(block_time, datetime) else int(block_time)
    head = struct.pack("<i", 0x20000000) + bytes(32) + node.msg + struct.pack("<II", when, 0x207FFFFF)
    nonce = 0
    while True:
        raw = head + struct.pack("<I", nonce)
        if BlockHeader(raw).pow_ok:
            break
        nonce += 1
    return serialize_ots(DetachedProof(digest, ts)), raw.hex()


# ===========================================================================
# Calendar stamping and the operator CLI (plan 06-16, FREEZE-03 / D-01).
#
# Scheme `fc-ots/v1`: what is stamped is the round's Merkle root (the `root` field
# of roots/<w>.json), not the hash of that file. fc/service.py rewrites <w>.json
# after anchoring, so a file hash would change under the proof; the Merkle root
# does not. Per window the operator keeps:
#   roots/<w>.root.ots     the proof (grows an upgraded Bitcoin attestation later)
#   roots/<w>.anchor.json  its status, the calendars used and, once confirmed, the block
#   roots/bitcoin-headers.json  verified headers, so a third party verifies offline
# ===========================================================================

DEFAULT_CALENDARS = (
    "https://a.pool.opentimestamps.org",
    "https://b.pool.opentimestamps.org",
    "https://a.pool.eternitywall.com",
    "https://ots.btc.catallaxy.com",
)
# Hosts a calendar URI may name: *.calendar.opentimestamps.org, ?.pool.opentimestamps.org,
# *.calendar.eternitywall.com, ?.pool.eternitywall.com, ots.btc.catallaxy.com.
CALENDAR_HOST_ALLOWLIST = re.compile(
    r"(?:[a-z0-9-]+\.)+calendar\.opentimestamps\.org"
    r"|[a-z0-9]\.pool\.opentimestamps\.org"
    r"|(?:[a-z0-9-]+\.)+calendar\.eternitywall\.com"
    r"|[a-z0-9]\.pool\.eternitywall\.com"
    r"|ots\.btc\.catallaxy\.com"
)
# https only, no userinfo, no query or fragment, port 443 at most: anything else fails closed.
_CALENDAR_URI = re.compile(r"https://(?P<host>[A-Za-z0-9.-]+)(?::443)?(?:/[A-Za-z0-9._~-]*)*")
CALENDAR_ACCEPT = "application/vnd.opentimestamps.v1"
CALENDAR_MAX_BYTES = 10240
DEFAULT_MIN_CALENDARS = 2


class StampError(RuntimeError):
    """Too few calendars accepted a digest, or a window could not be stamped."""


class CalendarNotAllowed(ValueError):
    """A calendar URI outside the allowlist, or not https (T-06-10)."""


def calendar_allowed(uri: str) -> bool:
    """True only for an https URI whose host is a known public OpenTimestamps calendar.

    Pending attestations inside a proof can name any URI, so following one blindly is
    SSRF; every network call to a calendar passes through this check.
    """
    if not isinstance(uri, str) or len(uri) > MAX_URI_BYTES:
        return False
    m = _CALENDAR_URI.fullmatch(uri)
    return m is not None and CALENDAR_HOST_ALLOWLIST.fullmatch(m.group("host").lower()) is not None


class _CalendarRedirects(urllib.request.HTTPRedirectHandler):
    """Follow a redirect only to another allow-listed calendar URL."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not calendar_allowed(newurl):
            raise urllib.error.URLError(
                f"refusing redirect outside the calendar allowlist: {newurl!r}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _calendar_request(url: str, data: bytes | None, timeout: float, max_bytes: int) -> bytes:
    if not calendar_allowed(url):
        raise CalendarNotAllowed(f"refusing calendar URL {url!r}")
    req = urllib.request.Request(
        url, data=data,
        headers={"User-Agent": f"future-challenge-fc/{__version__}", "Accept": CALENDAR_ACCEPT},
    )
    opener = urllib.request.build_opener(_CalendarRedirects)
    with opener.open(req, timeout=timeout) as resp:
        body = resp.read(max_bytes + 1)
    if len(body) > max_bytes:
        raise ValueError(f"response from {url} exceeds {max_bytes} bytes")
    return body


def http_post(url: str, body: bytes, timeout: float = 20.0,
              max_bytes: int = CALENDAR_MAX_BYTES) -> bytes:
    """POST `body` to an allow-listed https calendar; oversize raises, never truncates."""
    return _calendar_request(url, body, timeout, max_bytes)


def calendar_get(url: str, timeout: float = 20.0, max_bytes: int = CALENDAR_MAX_BYTES) -> bytes:
    """GET from an allow-listed https calendar, sending the OpenTimestamps Accept header."""
    return _calendar_request(url, None, timeout, max_bytes)


def _validated_calendars(calendars, min_calendars: int) -> tuple[str, ...]:
    """Normalise, dedupe and check the calendar list before any request is made."""
    cleaned = tuple(dict.fromkeys(str(c).rstrip("/") for c in calendars))
    refused = [c for c in cleaned if not calendar_allowed(c)]
    if refused:
        raise CalendarNotAllowed("refusing calendar(s) outside the https allowlist: "
                                 + ", ".join(refused))
    if min_calendars < 1:
        raise StampError("min_calendars must be at least 1")
    if len(cleaned) < min_calendars:
        raise StampError(f"{len(cleaned)} calendar(s) given but {min_calendars} are required")
    return cleaned


@dataclass
class StampedDigest:
    """A digest's merged calendar timestamp and the calendars that produced it."""

    timestamp: Timestamp
    calendars: list[str]


def _parse_calendar_timestamp(body: bytes, msg: bytes) -> Timestamp:
    r = _Reader(body)
    ts = Timestamp.parse(r, msg)
    if not r.done:
        raise ValueError("trailing bytes after the calendar timestamp")
    if not any(isinstance(att, Pending | Bitcoin) for _m, att in ts.walk()):
        raise ValueError("calendar timestamp carries no pending or Bitcoin attestation")
    return ts


def stamp_digest(digest: bytes, calendars=DEFAULT_CALENDARS,
                 min_calendars: int = DEFAULT_MIN_CALENDARS, http_post=http_post,
                 timeout: float = 20.0) -> StampedDigest:
    """Submit `digest` to every calendar and merge the timestamps they return.

    Raises StampError unless at least `min_calendars` accepted it. A calendar that is
    down, slow or answers with garbage is skipped, never trusted blindly: the response
    is parsed under the same bounds as any proof.
    """
    if len(digest) != 32:
        raise ValueError("a digest to stamp must be 32 bytes")
    cals = _validated_calendars(calendars, min_calendars)
    root = Timestamp(bytes(digest))
    accepted: list[str] = []
    failures: list[str] = []
    for cal in cals:
        try:
            body = http_post(f"{cal}/digest", bytes(digest), timeout, CALENDAR_MAX_BYTES)
            ts = _parse_calendar_timestamp(body, bytes(digest))
        except (OSError, ValueError, http.client.HTTPException) as exc:
            failures.append(f"{cal} ({type(exc).__name__})")
            continue
        root.merge(ts)
        accepted.append(cal)
    if len(accepted) < min_calendars:
        raise StampError(
            f"only {len(accepted)} of {min_calendars} required calendars accepted the digest; "
            "failed: " + ", ".join(failures))
    return StampedDigest(root, accepted)


# ------------------------------------------------------------------ round directories


def _json_bytes(obj) -> bytes:
    return (json.dumps(obj, indent=2, sort_keys=True) + "\n").encode()


def _atomic_write(path: Path, data: bytes) -> None:
    """Write beside the target then rename, so a crash never leaves a torn file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _window_indices(roots_dir: Path) -> list[int]:
    found = []
    for p in roots_dir.glob("*.json"):
        if p.stem.isdigit() and p.stem == str(int(p.stem)):
            found.append(int(p.stem))
    return sorted(found)


def _window_root(roots_dir: Path, window: int) -> bytes:
    """The Merkle root of roots/<window>.json, the value that is stamped."""
    try:
        doc = json.loads((roots_dir / f"{window}.json").read_text())
        root = bytes.fromhex(doc["root"])
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError(f"roots/{window}.json has no usable 'root' field") from exc
    if len(root) != 32:
        raise ValueError(f"roots/{window}.json 'root' is not 32 bytes")
    return root


def stamp_round_dir(round_dir, calendars=DEFAULT_CALENDARS,
                    min_calendars: int = DEFAULT_MIN_CALENDARS, http_post=http_post,
                    timeout: float = 20.0, now: datetime | None = None) -> list[int]:
    """Stamp every roots/<w>.json that has no roots/<w>.root.ots yet.

    Writes roots/<w>.anchor.json first, then roots/<w>.root.ots (a window counts as
    stamped only once the .ots exists, so a crash between the two is retried) and
    never rewrites <w>.json. Returns the windows stamped by this call. Windows that
    could not be stamped are reported together in a StampError after the others
    were still attempted; a disallowed calendar raises CalendarNotAllowed first.
    """
    roots_dir = Path(round_dir) / "roots"
    cals = _validated_calendars(calendars, min_calendars)
    stamped: list[int] = []
    failures: list[str] = []
    for window in _window_indices(roots_dir):
        ots_path = roots_dir / f"{window}.root.ots"
        if ots_path.exists():
            continue
        try:
            root = _window_root(roots_dir, window)
            result = stamp_digest(root, cals, min_calendars, http_post, timeout)
            proof = serialize_ots(DetachedProof(root, result.timestamp))
        except (StampError, ValueError, OSError) as exc:
            failures.append(f"window {window}: {exc}")
            continue
        anchor = {
            "schema_version": C.SCHEMA_VERSION,
            "scheme": ANCHOR_SCHEME,
            "digest": root.hex(),
            "status": PENDING_CALENDAR,
            "calendar_uris": result.calendars,
            "stamped_at": C.iso_z(now or datetime.now(UTC)),
        }
        _atomic_write(roots_dir / f"{window}.anchor.json", _json_bytes(anchor))
        _atomic_write(ots_path, proof)
        stamped.append(window)
    if failures:
        raise StampError("; ".join(failures))
    return stamped


# ------------------------------------------------------------------------- upgrading

# (level, message) with level in {"info", "warning", "error"}. Network trouble is a
# warning (a calendar being down never fails a sweep); an unreadable or inconsistent
# file on disk is an error.
Note = tuple[str, str]


class UpgradeCounts(NamedTuple):
    confirmed: int  # windows whose proof is verified against a Bitcoin header (now or before)
    pending: int  # stamped windows still waiting for Bitcoin
    upgraded: int  # windows promoted to bitcoin by this call


def _short(text: str, limit: int = 80) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."


def upgrade_timestamp(ts: Timestamp, http_get=calendar_get, timeout: float = 20.0,
                      notes: list[Note] | None = None) -> int:
    """Ask each pending attestation's calendar for its completed timestamp.

    GET {uri}/timestamp/{hex(msg at the pending node)}: 200 is parsed and merged into
    that node, 404 means still pending, anything else is noted and skipped. Pending
    attestations are kept. A URI that is not an allow-listed https calendar is never
    requested (T-06-10: a proof can name any URI). Returns how many nodes gained
    something.
    """
    notes = [] if notes is None else notes
    targets = [(node, att) for node in ts.walk_nodes() for att in node.attestations
               if isinstance(att, Pending)]
    upgraded = 0
    for node, att in targets:
        if not calendar_allowed(att.uri):
            shown = _short(repr(att.uri))
            notes.append(("warning", f"not contacting pending URI outside the allowlist: {shown}"))
            continue
        if node.msg is None:
            notes.append((
                "warning",
                f"cannot query {att.uri}: its message needs an operation this build cannot run",
            ))
            continue
        url = f"{att.uri.rstrip('/')}/timestamp/{node.msg.hex()}"
        try:
            body = http_get(url, timeout, CALENDAR_MAX_BYTES)
            got = _parse_calendar_timestamp(body, node.msg)
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                notes.append(("warning", f"{att.uri}: HTTP {exc.code}"))
            continue
        except (OSError, ValueError, http.client.HTTPException) as exc:
            notes.append(("warning", f"{att.uri}: {type(exc).__name__}"))
            continue
        before = node.serialize()
        node.merge(got)
        if node.serialize() != before:
            upgraded += 1
    return upgraded


class _MemoHeaders:
    """Ask the wrapped header source once per height."""

    def __init__(self, source) -> None:
        self.source = source
        self._seen: dict[int, BlockHeader | None] = {}

    def header(self, height: int) -> BlockHeader | None:
        if height not in self._seen:
            self._seen[height] = self.source.header(height)
        return self._seen[height]


def _read_json_dict(path: Path) -> dict:
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    if isinstance(data, dict):
        return data
    raise ValueError(f"{path.name} is not a JSON object")


def _record_header(roots_dir: Path, height: int, hdr: BlockHeader) -> None:
    """Merge {height: {hash, header}} into roots/bitcoin-headers.json (sorted keys)."""
    path = roots_dir / "bitcoin-headers.json"
    data = _read_json_dict(path)
    entry = {"hash": hdr.block_hash_hex, "header": hdr.raw.hex()}
    if data.get(str(height)) != entry:
        data[str(height)] = entry
        _atomic_write(path, _json_bytes(data))


def upgrade_round_dir(round_dir, http_get=calendar_get, headers=None, timeout: float = 20.0,
                      *, notes: list[Note] | None = None,
                      now: datetime | None = None) -> UpgradeCounts:
    """Upgrade every stamped window that is not yet Bitcoin-confirmed.

    For each roots/<w>.root.ots whose anchor.json status is not bitcoin: fetch completed
    attestations from the proof's calendars, rewrite the .ots if it grew, and, when it
    now verifies against a Bitcoin header from `headers` (default: two public explorers
    that must agree), mark anchor.json bitcoin and commit the header to
    roots/bitcoin-headers.json so a third party can verify offline. A proof that grew an
    attestation which does not verify is not kept. <w>.json is never touched.
    """
    notes = [] if notes is None else notes
    roots_dir = Path(round_dir) / "roots"
    source = _MemoHeaders(headers if headers is not None else ExplorerHeaders())
    confirmed = pending = upgraded = 0
    for window in _window_indices(roots_dir):
        ots_path = roots_dir / f"{window}.root.ots"
        if not ots_path.exists():
            continue
        anchor_path = roots_dir / f"{window}.anchor.json"
        try:
            anchor = _read_json_dict(anchor_path)
            if anchor.get("status") == BITCOIN:
                confirmed += 1
                continue
            root = _window_root(roots_dir, window)
            original = ots_path.read_bytes()
            proof = parse_ots(original)
            if proof.digest != root:
                raise ValueError("proof digest does not match the window's Merkle root")
        except (OSError, ValueError) as exc:
            notes.append(("error", f"window {window}: {type(exc).__name__}: {exc}"))
            pending += 1
            continue

        grew = upgrade_timestamp(proof.timestamp, http_get, timeout, notes) > 0
        has_bitcoin = any(isinstance(att, Bitcoin) for _m, att in proof.timestamp.walk())
        verdict = verify_digest_anchor(root, proof, source) if has_bitcoin else None
        if verdict is not None and verdict.status == INVALID:
            notes.append(("warning", f"window {window}: attestation rejected: {verdict.detail}"))
            pending += 1
            continue
        try:
            if grew:
                _atomic_write(ots_path, serialize_ots(proof))
            if verdict is not None and verdict.confirmed and verdict.height is not None:
                hdr = source.header(verdict.height)
                if hdr is None:  # cannot happen after a verified verdict; fail safe
                    raise ValueError(f"header for block {verdict.height} vanished")
                _record_header(roots_dir, verdict.height, hdr)
                anchor.update({
                    "schema_version": C.SCHEMA_VERSION,
                    "scheme": ANCHOR_SCHEME,
                    "digest": root.hex(),
                    "status": BITCOIN,
                    "bitcoin_block_height": verdict.height,
                    "block_hash": verdict.block_hash,
                    "upgraded_at": C.iso_z(now or datetime.now(UTC)),
                })
                anchor.setdefault("calendar_uris", verdict.calendar_uris)
                _atomic_write(anchor_path, _json_bytes(anchor))
                line = f"upgraded {anchor_path} {BITCOIN} height {verdict.height}"
                notes.append(("info", f"{line} block {verdict.block_hash}"))
                confirmed += 1
                upgraded += 1
                continue
        except (OSError, ValueError) as exc:
            notes.append(("error", f"window {window}: {type(exc).__name__}: {exc}"))
        pending += 1
    return UpgradeCounts(confirmed, pending, upgraded)


def verify_round_window(round_dir, window: int, headers) -> AnchorVerdict:
    """Verify roots/<window>.root.ots against the window's Merkle root and `headers`."""
    roots_dir = Path(round_dir) / "roots"
    root = _window_root(roots_dir, window)
    ots_path = roots_dir / f"{window}.root.ots"
    if not ots_path.exists():
        return AnchorVerdict(NOT_ANCHORED, detail=f"no proof roots/{window}.root.ots")
    try:
        proof = parse_ots(ots_path.read_bytes())
    except ValueError as exc:
        return AnchorVerdict(INVALID, detail=f"unreadable proof: {exc}")
    return verify_digest_anchor(root, proof, headers)


# ------------------------------------------------------------------------------ CLI


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m fc.anchor",
        description="Stamp, upgrade and verify the Merkle roots of a round (scheme fc-ots/v1).")
    sub = parser.add_subparsers(dest="command", required=True)

    stamp = sub.add_parser("stamp", help="submit each unstamped root to public calendars")
    stamp.add_argument("--round-dir", nargs="+", required=True, metavar="DIR")
    stamp.add_argument("--calendar", action="append", metavar="URL",
                       help="https calendar URL (repeatable); default: the four public calendars")
    stamp.add_argument("--min-calendars", type=int, default=DEFAULT_MIN_CALENDARS)
    stamp.add_argument("--timeout", type=float, default=20.0)

    upgrade = sub.add_parser(
        "upgrade", help="fetch completed Bitcoin attestations and record verified headers")
    upgrade.add_argument("--round-dir", nargs="+", required=True, metavar="DIR")
    upgrade.add_argument("--timeout", type=float, default=20.0)

    verify = sub.add_parser("verify", help="exit 0 iff a window's proof verifies against Bitcoin")
    verify.add_argument("--round-dir", required=True, metavar="DIR")
    verify.add_argument("--window", type=int, required=True)
    verify.add_argument("--headers", metavar="FILE",
                        help="header file (default: DIR/roots/bitcoin-headers.json)")
    verify.add_argument("--fetch", action="store_true",
                        help="also ask two public explorers for headers the file lacks")
    return parser


def _cmd_stamp(args, http_post) -> int:
    calendars = tuple(args.calendar) if args.calendar else DEFAULT_CALENDARS
    try:
        _validated_calendars(calendars, args.min_calendars)
    except (CalendarNotAllowed, StampError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    failed = False
    for round_dir in args.round_dir:
        roots_dir = Path(round_dir) / "roots"
        if not roots_dir.is_dir():
            print(f"error: {roots_dir}: no such directory", file=sys.stderr)
            failed = True
            continue
        try:
            stamped = stamp_round_dir(round_dir, calendars, args.min_calendars, http_post,
                                      args.timeout)
        except StampError as exc:
            print(f"unstamped {roots_dir}: {exc}", file=sys.stderr)
            failed = True
            continue
        for window in stamped:
            print(f"stamped {roots_dir / f'{window}.root.ots'} {PENDING_CALENDAR}")
        if not stamped:
            print(f"nothing to stamp in {roots_dir}")
    return 1 if failed else 0


def _cmd_upgrade(args, http_get, headers) -> int:
    failed = False
    totals = [0, 0, 0]
    for round_dir in args.round_dir:
        roots_dir = Path(round_dir) / "roots"
        if not roots_dir.is_dir():
            print(f"error: {roots_dir}: no such directory", file=sys.stderr)
            failed = True
            continue
        notes: list[Note] = []
        counts = upgrade_round_dir(round_dir, http_get, headers, args.timeout, notes=notes)
        for level, message in notes:
            if level == "info":
                print(message)
            else:
                print(f"{level}: {message}", file=sys.stderr)
                failed = failed or level == "error"
        totals = [a + b for a, b in zip(totals, counts, strict=True)]
    print(f"confirmed {totals[0]}, pending {totals[1]}, upgraded {totals[2]}")
    return 1 if failed else 0


def _cmd_verify(args, explorer) -> int:
    roots_dir = Path(args.round_dir) / "roots"
    file_path = Path(args.headers) if args.headers else roots_dir / "bitcoin-headers.json"
    source = HeaderFile(file_path)
    if args.fetch:
        source = ChainedHeaders(source, explorer if explorer is not None else ExplorerHeaders())
    try:
        verdict = verify_round_window(args.round_dir, args.window, source)
    except (OSError, ValueError) as exc:
        print(f"error: window {args.window}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    if verdict.confirmed and verdict.anchored_before is not None:
        print(f"window {args.window}: {verdict.status} height {verdict.height} "
              f"block {verdict.block_hash} anchored_before {C.iso_z(verdict.anchored_before)}")
        return 0
    print(f"window {args.window}: {verdict.status} {verdict.detail}")
    return 1


def main(argv: list[str] | None = None, *, http_post=http_post, http_get=calendar_get,
         headers=None) -> int:
    """`python -m fc.anchor stamp|upgrade|verify`.

    HTTP and the Bitcoin header source are injectable so tests never open a socket;
    `headers=None` means two public explorers that must agree.
    """
    args = _build_parser().parse_args(argv)
    if args.command == "stamp":
        return _cmd_stamp(args, http_post)
    if args.command == "upgrade":
        return _cmd_upgrade(args, http_get, headers)
    if args.command == "verify":
        return _cmd_verify(args, headers)
    return 2  # pragma: no cover - argparse rejects unknown subcommands


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
