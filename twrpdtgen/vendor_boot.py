#
# Copyright (C) 2026 The Android Open Source Project
#
# SPDX-License-Identifier: Apache-2.0
#
"""Read Android vendor_boot images without relying on Android Image Kitchen."""

from dataclasses import dataclass
from gzip import decompress as gzip_decompress
from lzma import decompress as lzma_decompress
from pathlib import Path
from shutil import which
from subprocess import PIPE, run
from tempfile import TemporaryDirectory
from typing import List, Tuple
import struct


VENDOR_BOOT_MAGIC = b"VNDRBOOT"
VENDOR_BOOT_HEADER_V3_SIZE = 2112
VENDOR_BOOT_HEADER_V4_SIZE = 2128
VENDOR_RAMDISK_TABLE_ENTRY_V4_SIZE = 108

VENDOR_RAMDISK_TYPE_NONE = 0
VENDOR_RAMDISK_TYPE_PLATFORM = 1
VENDOR_RAMDISK_TYPE_RECOVERY = 2
VENDOR_RAMDISK_TYPE_DLKM = 3

LZ4_LEGACY_MAGIC = b"\x02\x21\x4c\x18"
LZ4_FRAME_MAGIC = b"\x04\x22\x4d\x18"
GZIP_MAGIC = b"\x1f\x8b"
XZ_MAGIC = b"\xfd7zXZ\x00"
CPIO_MAGICS = (b"070701", b"070702")


def _align(size: int, alignment: int) -> int:
	return (size + alignment - 1) // alignment * alignment


@dataclass(frozen=True)
class VendorRamdiskFragment:
	size: int
	offset: int
	fragment_type: int
	name: str


@dataclass
class VendorBootInfo:
	"""The vendor_boot fields required to recreate the image."""
	image: Path
	header_version: int
	page_size: int
	kernel_addr: int
	ramdisk_addr: int
	tags_addr: int
	dtb_addr: int
	vendor_ramdisk_size: int
	dtb_size: int
	vendor_cmdline: str
	bootconfig_lines: Tuple[str, ...]
	ramdisk_compression: str
	ramdisk: Path
	dtb: Path
	fragments: Tuple[VendorRamdiskFragment, ...]

	@property
	def image_size(self) -> int:
		return self.image.stat().st_size

	@property
	def kernel_addr_hex(self) -> str:
		return f"0x{self.kernel_addr:08x}"

	@property
	def ramdisk_addr_hex(self) -> str:
		return f"0x{self.ramdisk_addr:08x}"

	@property
	def tags_addr_hex(self) -> str:
		return f"0x{self.tags_addr:08x}"

	@property
	def dtb_addr_hex(self) -> str:
		return f"0x{self.dtb_addr:08x}"

	@property
	def uses_lz4(self) -> bool:
		return self.ramdisk_compression == "lz4"


class VendorBootImage:
	"""Parse and unpack an Android vendor_boot v3 or v4 image."""

	def __init__(self, image: Path):
		self.image = image
		self.tempdir = TemporaryDirectory(prefix="twrpdtgen-vendor-boot-")
		self.path = Path(self.tempdir.name)
		self.ramdisk_path = self.path / "ramdisk"
		self.dtb_path = self.path / "dtb.img"
		self.info = self._unpack()

	@staticmethod
	def is_vendor_boot(image: Path) -> bool:
		with image.open("rb") as image_file:
			return image_file.read(len(VENDOR_BOOT_MAGIC)) == VENDOR_BOOT_MAGIC

	def cleanup(self):
		self.tempdir.cleanup()

	def _unpack(self) -> VendorBootInfo:
		image_data = self.image.read_bytes()
		if len(image_data) < VENDOR_BOOT_HEADER_V3_SIZE:
			raise ValueError("vendor_boot image is smaller than its header")
		if image_data[:len(VENDOR_BOOT_MAGIC)] != VENDOR_BOOT_MAGIC:
			raise ValueError("not an Android vendor_boot image")

		header_version = self._u32(image_data, 8)
		if header_version not in (3, 4):
			raise ValueError(f"unsupported vendor_boot header version: {header_version}")

		minimum_header_size = (
			VENDOR_BOOT_HEADER_V4_SIZE if header_version == 4 else VENDOR_BOOT_HEADER_V3_SIZE
		)
		if len(image_data) < minimum_header_size:
			raise ValueError("vendor_boot image has a truncated header")

		page_size = self._u32(image_data, 12)
		if page_size == 0 or page_size & (page_size - 1):
			raise ValueError(f"invalid vendor_boot page size: {page_size}")

		kernel_addr = self._u32(image_data, 16)
		ramdisk_addr = self._u32(image_data, 20)
		vendor_ramdisk_size = self._u32(image_data, 24)
		vendor_cmdline = self._cstring(image_data[28:28 + 2048])
		tags_addr = self._u32(image_data, 2076)
		header_size = self._u32(image_data, 2096)
		dtb_size = self._u32(image_data, 2100)
		dtb_addr = self._u64(image_data, 2104)

		if header_size < minimum_header_size or header_size > page_size:
			raise ValueError(f"invalid vendor_boot header size: {header_size}")

		ramdisk_offset = page_size
		dtb_offset = ramdisk_offset + _align(vendor_ramdisk_size, page_size)
		self._require_range(image_data, ramdisk_offset, vendor_ramdisk_size, "vendor ramdisk")
		self._require_range(image_data, dtb_offset, dtb_size, "DTB")
		self.dtb_path.write_bytes(image_data[dtb_offset:dtb_offset + dtb_size])

		bootconfig_lines: Tuple[str, ...] = ()
		if header_version == 4:
			fragments, bootconfig_lines = self._parse_v4_fragments(
				image_data, ramdisk_offset, vendor_ramdisk_size, dtb_offset, dtb_size, page_size
			)
		else:
			fragments = (VendorRamdiskFragment(
				size=vendor_ramdisk_size,
				offset=0,
				fragment_type=VENDOR_RAMDISK_TYPE_PLATFORM,
				name="",
			),)

		self.ramdisk_path.mkdir()
		compression = "none"
		for fragment in fragments:
			if fragment.size == 0:
				continue
			if fragment.offset + fragment.size > vendor_ramdisk_size:
				raise ValueError("vendor ramdisk fragment exceeds the declared ramdisk size")
			payload_start = ramdisk_offset + fragment.offset
			payload = image_data[payload_start:payload_start + fragment.size]
			compression = self._extract_fragment(payload, compression)

		return VendorBootInfo(
			image=self.image,
			header_version=header_version,
			page_size=page_size,
			kernel_addr=kernel_addr,
			ramdisk_addr=ramdisk_addr,
			tags_addr=tags_addr,
			dtb_addr=dtb_addr,
			vendor_ramdisk_size=vendor_ramdisk_size,
			dtb_size=dtb_size,
			vendor_cmdline=vendor_cmdline,
			bootconfig_lines=bootconfig_lines,
			ramdisk_compression=compression,
			ramdisk=self.ramdisk_path,
			dtb=self.dtb_path,
			fragments=fragments,
		)

	def _parse_v4_fragments(self, image_data: bytes, ramdisk_offset: int,
							vendor_ramdisk_size: int, dtb_offset: int, dtb_size: int,
							page_size: int) -> Tuple[Tuple[VendorRamdiskFragment, ...], Tuple[str, ...]]:
		table_size = self._u32(image_data, 2112)
		entry_count = self._u32(image_data, 2116)
		entry_size = self._u32(image_data, 2120)
		bootconfig_size = self._u32(image_data, 2124)
		if entry_count and entry_size < VENDOR_RAMDISK_TABLE_ENTRY_V4_SIZE:
			raise ValueError("vendor ramdisk table entry is smaller than the v4 layout")
		if entry_count * entry_size > table_size:
			raise ValueError("vendor ramdisk table is truncated")

		table_offset = dtb_offset + _align(dtb_size, page_size)
		self._require_range(image_data, table_offset, table_size, "vendor ramdisk table")
		bootconfig_offset = table_offset + _align(table_size, page_size)
		self._require_range(image_data, bootconfig_offset, bootconfig_size, "vendor bootconfig")

		fragments: List[VendorRamdiskFragment] = []
		for index in range(entry_count):
			entry_offset = table_offset + index * entry_size
			fragments.append(VendorRamdiskFragment(
				size=self._u32(image_data, entry_offset),
				offset=self._u32(image_data, entry_offset + 4),
				fragment_type=self._u32(image_data, entry_offset + 8),
				name=self._cstring(image_data[entry_offset + 12:entry_offset + 44]),
			))

		if not fragments and vendor_ramdisk_size:
			raise ValueError("vendor_boot v4 contains a ramdisk without a fragment table")

		bootconfig = image_data[bootconfig_offset:bootconfig_offset + bootconfig_size]
		bootconfig_lines = tuple(
			line for line in bootconfig.decode("utf-8", errors="replace").splitlines() if line
		)
		return tuple(fragments), bootconfig_lines

	def _extract_fragment(self, payload: bytes, current_compression: str) -> str:
		compression, cpio_payload = self._decompress(payload)
		if not cpio_payload.startswith(CPIO_MAGICS):
			raise ValueError("vendor ramdisk fragment is not a newc CPIO archive")

		try:
			result = run(
				["cpio", "--quiet", "-idm", "--no-absolute-filenames"],
				cwd=self.ramdisk_path,
				input=cpio_payload,
				stdout=PIPE,
				stderr=PIPE,
				check=False,
			)
		except FileNotFoundError as error:
			raise RuntimeError("cpio is required to unpack vendor_boot ramdisks") from error
		if result.returncode != 0:
			raise RuntimeError(result.stderr.decode("utf-8", errors="replace").strip())

		return compression if current_compression == "none" else current_compression

	def _decompress(self, payload: bytes) -> Tuple[str, bytes]:
		if payload.startswith(CPIO_MAGICS):
			return "none", payload
		if payload.startswith(GZIP_MAGIC):
			return "gzip", gzip_decompress(payload)
		if payload.startswith(XZ_MAGIC):
			return "xz", lzma_decompress(payload)
		if payload.startswith((LZ4_LEGACY_MAGIC, LZ4_FRAME_MAGIC)):
			if which("lz4") is None:
				raise RuntimeError("lz4 is required to unpack this vendor_boot ramdisk")
			result = run(["lz4", "-d", "-c"], input=payload, stdout=PIPE,
						 stderr=PIPE, check=False)
			if result.returncode != 0:
				raise RuntimeError(result.stderr.decode("utf-8", errors="replace").strip())
			return "lz4", result.stdout
		raise ValueError("unknown vendor ramdisk compression")

	@staticmethod
	def _u32(data: bytes, offset: int) -> int:
		return struct.unpack_from("<I", data, offset)[0]

	@staticmethod
	def _u64(data: bytes, offset: int) -> int:
		return struct.unpack_from("<Q", data, offset)[0]

	@staticmethod
	def _cstring(data: bytes) -> str:
		return data.split(b"\0", 1)[0].decode("utf-8", errors="replace")

	@staticmethod
	def _require_range(data: bytes, offset: int, size: int, name: str):
		if offset < 0 or size < 0 or offset + size > len(data):
			raise ValueError(f"{name} exceeds the vendor_boot image")
