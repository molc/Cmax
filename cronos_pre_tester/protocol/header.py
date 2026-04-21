import struct
from dataclasses import dataclass

PROTOCOL_MARKER = 0xFFFF
PROTOCOL_VERSION = 0x0001
BODY_TYPE_XML = 0
BODY_TYPE_BINARY = 1
HEADER_SIZE = 24

HEADER_FMT = "!HHIIIII"  # network big-endian


@dataclass
class ProtocolHeader:
    marker: int = PROTOCOL_MARKER
    version: int = PROTOCOL_VERSION
    protocol_num: int = 0
    serial: int = 0
    status_code: int = 0
    body_length: int = 0
    body_type: int = BODY_TYPE_XML

    def pack(self) -> bytes:
        self.body_length = 0  # will be set by caller
        return struct.pack(
            HEADER_FMT,
            self.marker,
            self.version,
            self.protocol_num,
            self.serial,
            self.status_code,
            self.body_length,
            self.body_type,
        )

    def pack_with_body(self, body_bytes: bytes) -> bytes:
        """Pack header with body, setting body_length automatically."""
        self.body_length = len(body_bytes)
        return struct.pack(
            HEADER_FMT,
            self.marker,
            self.version,
            self.protocol_num,
            self.serial,
            self.status_code,
            self.body_length,
            self.body_type,
        )

    @classmethod
    def unpack(cls, data: bytes) -> "ProtocolHeader":
        if len(data) != HEADER_SIZE:
            raise ValueError(f"Header must be {HEADER_SIZE} bytes, got {len(data)}")
        marker, version, proto, serial, status, length, body_type = struct.unpack(
            HEADER_FMT, data
        )
        return cls(
            marker=marker,
            version=version,
            protocol_num=proto,
            serial=serial,
            status_code=status,
            body_length=length,
            body_type=body_type,
        )

    def __str__(self) -> str:
        return (
            f"ProtocolHeader(marker=0x{self.marker:04X}, "
            f"proto={self.protocol_num}, serial={self.serial}, "
            f"status={self.status_code}, body_len={self.body_length}, "
            f"body_type={self.body_type})"
        )
