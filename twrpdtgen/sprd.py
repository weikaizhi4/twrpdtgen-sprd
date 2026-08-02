#
# Copyright (C) 2026 The Android Open Source Project
#
# SPDX-License-Identifier: Apache-2.0
#
"""SPRD/Unisoc-specific build settings derived from stock properties."""

from dataclasses import dataclass
from pathlib import Path
from re import compile as re_compile, search
from struct import unpack_from
from typing import Optional


def _first_prop(build_prop, *names: str) -> Optional[str]:
	for name in names:
		value = build_prop.get_prop(name)
		if value:
			return value
	return None


def _android_major(release: str) -> int:
	match = search(r"\d+", release)
	if not match:
		raise ValueError(f"cannot determine Android version from {release!r}")
	return int(match.group())


_HIMAX_MARKER = re_compile(r"(?:himax|hxchipset|hx8\d{3,})", flags=0)


def _parse_sprd_panel_fdt(data: bytes, base: int) -> Optional[tuple]:
	"""Read a panel resolution from one flattened device-tree blob."""
	if base < 0 or len(data) - base < 40:
		return None

	try:
		_, total_size, struct_offset, strings_offset, _, _, _, _, _, _ = unpack_from(
			">10I", data, base
		)
	except ValueError:
		return None
	end = base + total_size
	struct_start = base + struct_offset
	strings_start = base + strings_offset
	if end > len(data) or not (base <= struct_start < end and base <= strings_start < end):
		return None

	def property_name(offset: int) -> Optional[str]:
		start = strings_start + offset
		if start >= end:
			return None
		stop = data.find(b"\0", start, end)
		if stop < 0:
			return None
		return data[start:stop].decode("ascii", errors="ignore")

	def aligned(offset: int) -> int:
		return (offset + 3) & ~3

	stack = []
	display_coords = None
	cursor = struct_start
	while cursor + 4 <= end:
		tag = unpack_from(">I", data, cursor)[0]
		cursor += 4
		if tag == 1:  # FDT_BEGIN_NODE
			stop = data.find(b"\0", cursor, end)
			if stop < 0:
				return None
			stack.append({})
			cursor = aligned(stop + 1)
		elif tag == 2:  # FDT_END_NODE
			if not stack:
				return display_coords
			node = stack.pop()
			if node.get("compatible") != b"sprd,generic-mipi-panel\0":
				coords = node.get("focaltech,display-coords") or node.get("display-coords")
				if coords is not None and len(coords) == 16:
					x0, y0, x1, y1 = unpack_from(">4I", coords)
					if x1 >= x0 and y1 >= y0:
						display_coords = (x1 - x0 + 1, y1 - y0 + 1)
				continue
			width = node.get("sprd,sr-width")
			height = node.get("sprd,sr-height")
			if width is not None and height is not None and len(width) == len(height) == 4:
				return unpack_from(">I", width)[0], unpack_from(">I", height)[0]
		elif tag == 3:  # FDT_PROP
			if not stack or cursor + 8 > end:
				return None
			length, name_offset = unpack_from(">II", data, cursor)
			cursor += 8
			if cursor + length > end:
				return None
			name = property_name(name_offset)
			if name is None:
				return None
			stack[-1][name] = data[cursor:cursor + length]
			cursor = aligned(cursor + length)
		elif tag == 4:  # FDT_NOP
			continue
		elif tag == 9:  # FDT_END
			break
		else:
			return None
	return display_coords


def _sprd_panel_screen_size(dtb: Optional[Path]) -> Optional[tuple]:
	"""Read panel resolution from a DTB or any FDT entry in a DTBO image."""
	if dtb is None or not dtb.is_file():
		return None

	data = dtb.read_bytes()
	magic = b"\xd0\x0d\xfe\xed"
	search_from = 0
	while True:
		base = data.find(magic, search_from)
		if base < 0:
			return None
		resolution = _parse_sprd_panel_fdt(data, base)
		if resolution is not None:
			return resolution
		# A DTBO contains several FDTs. Continue after malformed or irrelevant
		# entries instead of treating the first one as the complete image.
		search_from = base + len(magic)


@dataclass(frozen=True)
class SprdBuildProfile:
	"""A TWRP source target selected from the factory vendor ramdisk."""
	android_release: str
	android_sdk: str
	security_patch: str
	vendor_security_patch: str
	shipping_api_level: str
	recovery_branch: str
	lunch_platform: str
	screen_width: Optional[int]
	screen_height: Optional[int]
	copy_stock_selinux: bool
	uses_sc27xx_haptics: bool
	needs_legacy_drm: bool
	uses_himax_touch: bool

	@property
	def lunch_suffix(self) -> str:
		return f"-{self.lunch_platform}" if self.lunch_platform else ""

	@classmethod
	def from_build_prop(cls, build_prop, ramdisk: Optional[Path] = None,
						platform: Optional[str] = None,
						dtb: Optional[Path] = None,
						dtbo: Optional[Path] = None) -> "SprdBuildProfile":
		android_release = _first_prop(
			build_prop,
			"ro.system.build.version.release",
			"ro.build.version.release",
			"ro.vendor.build.version.release",
		)
		if android_release is None:
			raise ValueError("stock properties do not contain an Android release")

		android_sdk = _first_prop(
			build_prop,
			"ro.system.build.version.sdk",
			"ro.build.version.sdk",
			"ro.vendor.build.version.sdk",
		) or ""
		security_patch = _first_prop(
			build_prop,
			"ro.system.build.version.security_patch",
			"ro.build.version.security_patch",
		) or ""
		vendor_security_patch = _first_prop(
			build_prop,
			"ro.vendor.build.version.security_patch",
		) or security_patch
		shipping_api_level = _first_prop(
			build_prop,
			"ro.vendor.build.version.sdk",
			"ro.product.first_api_level",
			"ro.board.first_api_level",
			"ro.vendor.api_level",
		) or android_sdk
		platform = (platform or _first_prop(build_prop, "ro.board.platform") or "").lower()
		uses_sc27xx_haptics = _has_sc27xx_haptics(ramdisk)
		uses_himax_touch = _has_himax_touch(ramdisk)
		# Unisoc's DRM implementation on the UMS family is not reliable with
		# atomic modesets in recovery. Keep the legacy CRTC path enabled for
		# UMS9621 as well; this is the path used by the known-good tree.
		needs_legacy_drm = platform.startswith("ums")
		screen_size = _sprd_panel_screen_size(dtb)
		if screen_size is None:
			screen_size = _sprd_panel_screen_size(dtbo)
		screen_width = screen_size[0] if screen_size else None
		screen_height = screen_size[1] if screen_size else None

		# The Android version in prop.default can come from the system image while
		# vendor_boot still carries an older vendor policy.  Preserve that policy
		# whenever it is actually present; deciding from the system version caused
		# UMS9621 recovery to lose its init/runtime dependencies.
		copy_stock_selinux = bool(ramdisk and (ramdisk / "sepolicy").is_file())
		major = _android_major(android_release)
		if major >= 14:
			return cls(
				android_release=android_release,
				android_sdk=android_sdk,
				security_patch=security_patch,
				vendor_security_patch=vendor_security_patch,
				shipping_api_level=shipping_api_level,
				recovery_branch="twrp-14.1",
				lunch_platform="ap2a",
				screen_width=screen_width,
				screen_height=screen_height,
				copy_stock_selinux=copy_stock_selinux,
				uses_sc27xx_haptics=uses_sc27xx_haptics,
				needs_legacy_drm=needs_legacy_drm,
				uses_himax_touch=uses_himax_touch,
			)
		return cls(
			android_release=android_release,
			android_sdk=android_sdk,
			security_patch=security_patch,
			vendor_security_patch=vendor_security_patch,
			shipping_api_level=shipping_api_level,
			recovery_branch="twrp-12.1",
			lunch_platform="",
			screen_width=screen_width,
			screen_height=screen_height,
			copy_stock_selinux=copy_stock_selinux,
			uses_sc27xx_haptics=uses_sc27xx_haptics,
			needs_legacy_drm=needs_legacy_drm,
			uses_himax_touch=uses_himax_touch,
		)


def _has_sc27xx_haptics(ramdisk: Optional[Path]) -> bool:
	"""Detect the Unisoc vibrator driver retained from the vendor ramdisk."""
	if ramdisk is None:
		return False

	modules = ramdisk / "lib" / "modules"
	if not modules.is_dir():
		return False

	for module in modules.rglob("*"):
		name = module.name.lower()
		if module.is_file() and "sc27" in name and (
			"vibra" in name or "vibrator" in name or "haptic" in name
		):
			return True
	return False


def _has_himax_touch(ramdisk: Optional[Path]) -> bool:
	"""Detect Himax touchscreen drivers retained in a vendor ramdisk."""
	if ramdisk is None or not ramdisk.is_dir():
		return False

	# Vendor images commonly expose names such as
	# panel-boe-hx83102e-vdo(-spi).ko. Restrict the marker to file names to
	# avoid matching unrelated binary strings from the rest of the ramdisk.
	for root in (
		ramdisk / "lib" / "modules",
		ramdisk / "vendor" / "lib" / "modules",
		ramdisk / "odm" / "lib" / "modules",
	):
		if not root.is_dir():
			continue
		for module in root.rglob("*"):
			if module.is_file() and _HIMAX_MARKER.search(module.name.lower()):
				return True
	return False


def is_required_vendor_ramdisk_root_file(name: str) -> bool:
	"""Return whether a top-level vendor_boot file must survive in TWRP."""
	return (
		name != "init.rc" and (
			name == "sepolicy" or
			name.endswith("_contexts") or
			(name.startswith("init.recovery.") and name.endswith(".rc")) or
			(name.startswith("ueventd") and name.endswith(".rc")) or
			name.endswith(".sh")
		)
	)
