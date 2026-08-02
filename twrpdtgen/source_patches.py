#
# Copyright (C) 2026 The Android Open Source Project
#
# SPDX-License-Identifier: Apache-2.0
#
"""Source overlay selection for generated Unisoc TWRP device trees."""

from pathlib import Path
from typing import Optional, Tuple

from twrpdtgen import module_path
from twrpdtgen.sprd import SprdBuildProfile


# Keep the existing Android 13-and-earlier overlay unchanged. Its files target
# the TWRP 12.1 source layout and must not be applied to TWRP 14.1.
TWRP_12_PATCHES = (
	"bootable/recovery/Android.mk",
	"bootable/recovery/data.cpp",
	"bootable/recovery/etc/init.rc",
	"bootable/recovery/partition.cpp",
	"bootable/recovery/partitionmanager.cpp",
	"system/core/fastboot/device/fastboot_device.cpp",
	"system/vold/KeyUtil.cpp",
	"system/vold/Keymaster.cpp",
	"system/vold/MetadataCrypt.cpp",
)

# The Android 14 overlay follows the TWRP 14.1 source layout. It is sourced
# from the supplied Hyper7s TWRP 14 tree and intentionally replaces, rather
# than extends, the TWRP 12.1 patch set.
TWRP_14_PATCHES = (
	"bootable/recovery/Android.mk",
	"bootable/recovery/libtar/Android.mk",
	"bootable/recovery/libtar/append.c",
	"bootable/recovery/libtar/block.c",
	"bootable/recovery/libtar/extract.c",
	"bootable/recovery/libtar/libtar.h",
	"bootable/recovery/libtar/output.c",
	"bootable/recovery/partition.cpp",
	"bootable/recovery/partitionmanager.cpp",
	"bootable/recovery/prebuilt/Android.mk",
	"bootable/recovery/twrpApex.cpp",
	"system/vold/KeyStorage.cpp",
	"cts/tests/tests/os/assets/platform_releases.txt",
)

# TWRP 14.1 builds only need these input haptics hooks when the factory
# vendor ramdisk exposes an SC27XX vibrator driver.
TWRP_14_SC27XX_HAPTICS_PATCHES = (
	"bootable/recovery/minuitwrp/events.cpp",
	"bootable/recovery/minuitwrp/libminuitwrp_defaults.go",
	"vendor/twrp/config/BoardConfigSoong.mk",
)

# TWRP 14.1 already contains the Unisoc DRM implementation, but the build
# variable must still be exported to Soong for UMS platforms.
TWRP_14_LEGACY_DRM_PATCHES = (
	"bootable/recovery/minuitwrp/libminuitwrp_defaults.go",
	"vendor/twrp/config/BoardConfigSoong.mk",
)

SC27XX_HAPTICS_PATCHES = (
	"bootable/recovery/minuitwrp/events.cpp",
	"bootable/recovery/minuitwrp/libminuitwrp_defaults.go",
	"vendor/twrp/config/BoardConfigSoong.mk",
)

LEGACY_DRM_PATCHES = (
	"bootable/recovery/minuitwrp/graphics_drm.cpp",
	"bootable/recovery/minuitwrp/libminuitwrp_defaults.go",
	"vendor/twrp/config/BoardConfigSoong.mk",
)

# The Himax touch fix is branch-neutral and intentionally lives in a separate
# overlay so non-Himax SC27XX/DRM selections keep their normal events.cpp.
HIMAX_TOUCH_PATCHES = (
	"bootable/recovery/minuitwrp/events.cpp",
)


def source_patch_root(profile: Optional[SprdBuildProfile] = None) -> Path:
	"""Locate the bundled source overlay for the selected TWRP branch."""
	directory = "SourcePatches14" if profile and profile.recovery_branch == "twrp-14.1" else "SourcePatches"
	for candidate in (module_path.parent / directory, module_path / directory):
		if candidate.is_dir():
			return candidate
	raise FileNotFoundError(f"Bundled {directory} directory was not found")


def himax_patch_root() -> Path:
	"""Locate the branch-neutral Himax source overlay."""
	for candidate in (module_path.parent / "HimaxPatches", module_path / "HimaxPatches"):
		if candidate.is_dir():
			return candidate
	raise FileNotFoundError("Bundled HimaxPatches directory was not found")


def source_patch_path(profile: SprdBuildProfile, relative_path: str) -> Path:
	"""Resolve a selected source file, including optional hardware overlays."""
	if (
		getattr(profile, "uses_himax_touch", False) and
		relative_path in HIMAX_TOUCH_PATCHES
	):
		return himax_patch_root() / relative_path
	return source_patch_root(profile) / relative_path


def selected_source_patches(profile: SprdBuildProfile) -> Tuple[str, ...]:
	"""Return source paths compatible with the selected TWRP branch."""
	if profile.recovery_branch == "twrp-14.1":
		patches = list(TWRP_14_PATCHES)
		if profile.needs_legacy_drm:
			patches.extend(TWRP_14_LEGACY_DRM_PATCHES)
		if profile.uses_sc27xx_haptics:
			patches.extend(TWRP_14_SC27XX_HAPTICS_PATCHES)
		if getattr(profile, "uses_himax_touch", False):
			patches.extend(HIMAX_TOUCH_PATCHES)
		return tuple(dict.fromkeys(patches))

	patches = list(TWRP_12_PATCHES)
	if profile.uses_sc27xx_haptics:
		patches.extend(SC27XX_HAPTICS_PATCHES)
	if profile.needs_legacy_drm:
		patches.extend(LEGACY_DRM_PATCHES)
	if getattr(profile, "uses_himax_touch", False):
		patches.extend(HIMAX_TOUCH_PATCHES)
	return tuple(dict.fromkeys(patches))
