"""External anchoring seam (DESIGN.md §6.2).

A published Merkle root is anchored two ways, and they are not equally strong:

- **OpenTimestamps** produces a proof that the root's digest existed before a
  given Bitcoin block. Nobody, including the hub, can backdate it. This is the
  trustless anchor and the only one allowed to carry an ordering claim.
- **Sigstore Rekor** records the root in a public transparency log within
  seconds. Its `integratedTime` is signed but sits outside Rekor v1's
  tamper-evident Merkle structure, so the log operator can alter it without
  detection. It is a fast corroborating signal, never proof of ordering.

Anchoring reaches third-party services, so it lives behind an injectable
callable: the service core and the unit suite never spawn a process or open a
socket. Plan 01-03 fills in the real OpenTimestamps and cosign implementations
behind these same seams without changing a caller.
"""

from __future__ import annotations

import hashlib
import json
import math
import struct
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

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
class OtsAttestation:
    """What a detached OpenTimestamps proof asserts about one digest."""

    status: str  # BITCOIN or PENDING_CALENDAR
    bitcoin_block_height: int | None
    calendar_uris: list[str]
    digest: str

    @property
    def confirmed(self) -> bool:
        return self.status == BITCOIN and self.bitcoin_block_height is not None


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


def _read_varuint(buf: bytes, offset: int) -> tuple[int, int]:
    val = 0
    shift = 0
    while offset < len(buf):
        b = buf[offset]
        offset += 1
        val |= (b & 0x7F) << shift
        if not (b & 0x80):
            return val, offset
        shift += 7
    raise ValueError("truncated varuint in OTS proof")


def parse_detached_timestamp(ots_bytes: bytes, target_digest: str) -> OtsAttestation:
    """Parse a detached `.ots` proof and report what it actually attests to.

    Supports both binary OpenTimestamps proofs (header magic + SHA-256 target digest +
    Bitcoin block header or pending calendar attestation tags) and JSON-encoded OTS
    attestation records.
    """
    if not ots_bytes:
        raise AnchorToolUnavailable("empty .ots proof")

    # 1. JSON-structured detached proof (used by offline tooling / CI fixtures)
    if ots_bytes.lstrip().startswith(b"{"):
        doc = json.loads(ots_bytes.decode("utf-8"))
        digest = doc.get("digest", "")
        if digest and digest != target_digest:
            raise ValueError(f"OTS digest {digest} != target {target_digest}")
        return OtsAttestation(
            status=doc.get("status", NOT_ANCHORED),
            bitcoin_block_height=doc.get("bitcoin_block_height"),
            calendar_uris=list(doc.get("calendar_uris", [])),
            digest=digest or target_digest,
        )

    # 2. Standard binary OpenTimestamps proof format
    if not ots_bytes.startswith(OTS_MAGIC):
        raise AnchorToolUnavailable(
            "unrecognized .ots binary header (expected OpenTimestamps proof magic)"
        )
    pos = len(OTS_MAGIC)
    version, pos = _read_varuint(ots_bytes, pos)
    if version != 1 or pos >= len(ots_bytes):
        raise ValueError(f"unsupported OTS version {version}")
    hash_op = ots_bytes[pos]
    pos += 1
    if hash_op != 0x08:  # SHA-256
        raise ValueError(f"unsupported OTS hash op 0x{hash_op:02x} (expected SHA-256 0x08)")
    digest_bytes = ots_bytes[pos : pos + 32]
    if len(digest_bytes) != 32:
        raise ValueError("truncated OTS target digest")
    digest_hex = digest_bytes.hex()
    if digest_hex != target_digest.lower():
        raise ValueError(f"OTS proof target digest {digest_hex} != {target_digest}")

    rest = ots_bytes[pos + 32 :]
    btc_idx = rest.find(OTS_BITCOIN_TAG)
    if btc_idx >= 0:
        payload_pos = btc_idx + len(OTS_BITCOIN_TAG)
        _payload_len, payload_pos = _read_varuint(rest, payload_pos)
        height, _ = _read_varuint(rest, payload_pos)
        return OtsAttestation(
            status=BITCOIN,
            bitcoin_block_height=height,
            calendar_uris=[],
            digest=digest_hex,
        )

    pend_idx = rest.find(OTS_PENDING_TAG)
    if pend_idx >= 0:
        uris: list[str] = []
        payload_pos = pend_idx + len(OTS_PENDING_TAG)
        try:
            _outer_len, payload_pos = _read_varuint(rest, payload_pos)
            uri_len, uri_pos = _read_varuint(rest, payload_pos)
            uri = rest[uri_pos : uri_pos + uri_len].decode("utf-8", errors="ignore")
            if uri.startswith("http"):
                uris.append(uri)
        except ValueError:
            uris = []
        return OtsAttestation(
            status=PENDING_CALENDAR,
            bitcoin_block_height=None,
            calendar_uris=uris,
            digest=digest_hex,
        )

    raise ValueError("no Bitcoin or Pending attestation found in .ots proof")


class FileHeaderSource:
    """Offline Bitcoin block-height to block-time lookup.

    An OpenTimestamps proof yields a block *height*, not a time. Resolving that
    to a timestamp normally needs a Bitcoin node — which a third party running
    `fc verify` offline does not have. The hub therefore publishes the heights
    it relies on beside the roots, and the verifier reads them from disk. A
    height that is absent resolves to None, which renders the anchor PENDING
    rather than silently passing.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._headers: dict[str, str] | None = None

    def _load(self) -> dict[str, str]:
        if self._headers is None:
            if not self.path.exists():
                self._headers = {}
            else:
                self._headers = json.loads(self.path.read_text())
        return self._headers

    def block_time(self, height: int) -> datetime | None:
        raw = self._load().get(str(height))
        if not raw:
            return None
        # Python 3.11+ parses a trailing "Z" directly.
        return datetime.fromisoformat(raw)


# ===========================================================================
# Real OpenTimestamps verification (plan 06-03, FREEZE-03).
#
# A minimal, stdlib-only implementation of the .ots wire format and of Bitcoin
# block-header checking. The legacy heuristic above is kept only until plans
# 06-07 and 06-13 move their consumers over; everything below is the real thing:
# it executes the operation chain from the file digest to each attestation and
# compares the result with a block header's merkle root.
# ===========================================================================

BITCOIN_TAG = OTS_BITCOIN_TAG
PENDING_TAG = OTS_PENDING_TAG
ANCHOR_SCHEME = "fc-ots/v1"

# Parser bounds (T-06-09): a proof is untrusted input that can arrive in a pull request.
MAX_PROOF_BYTES = 65536
MAX_DEPTH = 256
MAX_ARG_BYTES = 4096  # one op argument; also the ceiling for any intermediate message
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
        if len(out) > MAX_ARG_BYTES:
            raise ValueError(f"OTS operation result exceeds {MAX_ARG_BYTES} bytes")
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
