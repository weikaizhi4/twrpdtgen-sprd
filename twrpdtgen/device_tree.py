#
# Copyright (C) 2022 The Android Open Source Project
#
# SPDX-License-Identifier: Apache-2.0
#

from datetime import datetime
from git import Repo
from os import chmod
from pathlib import Path
from sebaubuntu_libs.libaik import AIKManager
from sebaubuntu_libs.libandroid.device_info import DeviceInfo
from sebaubuntu_libs.libandroid.fstab import Fstab
from sebaubuntu_libs.libandroid.props import BuildProp
from sebaubuntu_libs.liblogging import LOGD
from shutil import copyfile, copytree, rmtree
from stat import S_IRWXU, S_IRGRP, S_IROTH, S_IXGRP, S_IXOTH
from re import fullmatch, sub
from twrpdtgen import __version__ as version
from twrpdtgen.sprd import SprdBuildProfile, is_required_vendor_ramdisk_root_file
from twrpdtgen.source_patches import selected_source_patches, source_patch_path
from twrpdtgen.templates import render_template
from typing import List
from twrpdtgen.vendor_boot import VendorBootImage
from twrpdtgen.legacy_boot import LegacyBootImage

BUILDPROP_LOCATIONS = [Path() / "default.prop",
                       Path() / "prop.default",]
BUILDPROP_LOCATIONS += [Path() / dir / "build.prop"
                        for dir in ["system", "vendor"]]
BUILDPROP_LOCATIONS += [Path() / dir / "etc" / "build.prop"
                        for dir in ["system", "vendor"]]

FSTAB_LOCATIONS = [Path() / "etc" / "recovery.fstab"]
FSTAB_LOCATIONS += [Path() / dir / "etc" / "recovery.fstab"
                    for dir in ["system", "vendor"]]

INIT_RC_LOCATIONS = [Path()]
INIT_RC_LOCATIONS += [Path() / dir / "etc" / "init"
                      for dir in ["system", "vendor"]]

class DeviceTree:
	"""
	A class representing a device tree

	It initialize a basic device tree structure
	and save the location of some important files
	"""
	def __init__(self, image: Path, codename: str = None,
				 manufacturer: str = None):
		"""Initialize the device tree class."""
		self.image = image
		self.aik_manager = None
		self.vendor_boot = None
		self.legacy_boot = None
		self.sprd_profile = None
		self.is_sprd_legacy_recovery = False

		self.current_year = str(datetime.now().year)

		# Check if the image exists
		if not self.image.is_file():
			raise FileNotFoundError("Specified file doesn't exist")

		# vendor_boot must be handled separately: AIK exposes it as a generic
		# ramdisk and loses the v4 fragment table and bootconfig information.
		if VendorBootImage.is_vendor_boot(image):
			self.vendor_boot = VendorBootImage(image)
			self.image_info = self.vendor_boot.info
		else:
			if LegacyBootImage.is_legacy_boot(image):
				self.legacy_boot = LegacyBootImage(image).info
			self.aik_manager = AIKManager()
			self.image_info = self.aik_manager.unpackimg(image)

		assert self.image_info.ramdisk, "Ramdisk not found"

		LOGD("Getting device infos...")
		self.build_prop = BuildProp()
		for build_prop in [self.image_info.ramdisk / location for location in BUILDPROP_LOCATIONS]:
			if not build_prop.is_file():
				continue

			self.build_prop.import_props(build_prop)

		self.device_info = DeviceInfo(self.build_prop)
		if codename:
			self.device_info.codename = codename
		if manufacturer:
			self.device_info.manufacturer = manufacturer
		if self._is_sprd_platform():
			image_dtbo = getattr(self.image_info, "dtbo", None)
			self.sprd_profile = SprdBuildProfile.from_build_prop(
				self.build_prop,
				ramdisk=self.image_info.ramdisk,
				platform=self.device_info.platform,
				dtb=self.image_info.dtb,
				dtbo=image_dtbo,
			)
			self.is_sprd_legacy_recovery = bool(
				self.legacy_boot and self.legacy_boot.header_version <= 2 and
				not self.device_info.device_is_ab
			)

		# Generate fstab
		fstab = None
		self.fstab_source = None
		for fstab_location in [self.image_info.ramdisk / location for location in FSTAB_LOCATIONS]:
			if not fstab_location.is_file():
				continue

			LOGD(f"Generating fstab using {fstab} as reference...")
			fstab = Fstab(fstab_location)
			self.fstab_source = fstab_location
			break

		if fstab is None:
			raise AssertionError("fstab not found")

		self.fstab = fstab

		# Search for init rc files
		self.init_rcs: List[Path] = []
		for init_rc_path in [self.image_info.ramdisk / location for location in INIT_RC_LOCATIONS]:
			if not init_rc_path.is_dir():
				continue

			self.init_rcs += [init_rc for init_rc in init_rc_path.iterdir()
			                  if init_rc.name.endswith(".rc") and init_rc.name != "init.rc"]

	def dump_to_folder(self, output_path: Path, git: bool = False) -> Path:
		if self.vendor_boot is not None:
			return self._dump_sprd_vendor_boot(output_path, git)

		device_tree_folder = output_path / self.device_info.manufacturer / self.device_info.codename
		prebuilt_path = device_tree_folder / "prebuilt"
		recovery_root_path = device_tree_folder / "recovery" / "root"

		LOGD("Creating device tree folders...")
		if device_tree_folder.is_dir():
			rmtree(device_tree_folder, ignore_errors=True)
		device_tree_folder.mkdir(parents=True)
		prebuilt_path.mkdir(parents=True)
		recovery_root_path.mkdir(parents=True)

		LOGD("Writing makefiles/blueprints")
		self._render_template(device_tree_folder, "Android.bp", comment_prefix="//")
		self._render_template(device_tree_folder, "Android.mk")
		if self.sprd_profile is not None:
			self._render_template(device_tree_folder, "sprd_AndroidProducts.mk",
				out_file="AndroidProducts.mk")
		else:
			self._render_template(device_tree_folder, "AndroidProducts.mk")
		self._render_template(device_tree_folder, "BoardConfig.mk")
		self._render_template(device_tree_folder, "device.mk")
		self._render_template(device_tree_folder, "extract-files.sh")
		if self.sprd_profile is not None:
			self._render_template(device_tree_folder, "sprd_product.mk",
				out_file=f"twrp_{self.device_info.codename}.mk")
		else:
			self._render_template(device_tree_folder, "omni_device.mk",
				out_file=f"omni_{self.device_info.codename}.mk")
		self._render_template(device_tree_folder, "README.md")
		self._render_template(device_tree_folder, "setup-makefiles.sh")
		if self.sprd_profile is not None:
			self._render_template(device_tree_folder, "sprd_vendorsetup.sh",
				out_file="vendorsetup.sh")
		else:
			self._render_template(device_tree_folder, "vendorsetup.sh")

		# Set permissions
		chmod(device_tree_folder / "extract-files.sh", S_IRWXU | S_IRGRP | S_IROTH)
		chmod(device_tree_folder / "setup-makefiles.sh", S_IRWXU | S_IRGRP | S_IROTH)

		LOGD("Copying kernel...")
		if self.image_info.kernel is not None:
			copyfile(self.image_info.kernel, prebuilt_path / "kernel")
		if self.image_info.dt is not None:
			copyfile(self.image_info.dt, prebuilt_path / "dt.img")
		if self.image_info.dtb is not None:
			copyfile(self.image_info.dtb, prebuilt_path / "dtb.img")
		image_dtbo = getattr(self.image_info, "dtbo", None)
		if image_dtbo is not None:
			copyfile(image_dtbo, prebuilt_path / "dtbo.img")

		LOGD("Copying fstab...")
		(device_tree_folder / "recovery.fstab").write_text(self.fstab.format(twrp=True))

		LOGD("Copying init scripts...")
		for init_rc in self.init_rcs:
			copyfile(init_rc, recovery_root_path / init_rc.name, follow_symlinks=True)

		if self.sprd_profile is not None:
			self._copy_sprd_vendor_ramdisk(recovery_root_path, self.image_info.ramdisk)
			if self.is_sprd_legacy_recovery:
				self._write_sprd_legacy_recovery_fstab(recovery_root_path)
			self._copy_sprd_source_patches(prebuilt_path / "sourcecode")

		if git:
			self._initialize_git_repo(device_tree_folder)

		return device_tree_folder

	def _dump_sprd_vendor_boot(self, output_path: Path, git: bool) -> Path:
		"""Generate a vendor_boot TWRP device tree for Unisoc devices."""
		device_tree_folder = output_path / self.device_info.manufacturer / self.device_info.codename
		prebuilt_path = device_tree_folder / "prebuilt"
		recovery_root_path = device_tree_folder / "recovery" / "root"

		LOGD("Creating SPRD vendor_boot device tree folders...")
		if device_tree_folder.is_dir():
			rmtree(device_tree_folder, ignore_errors=True)
		prebuilt_path.mkdir(parents=True)
		recovery_root_path.mkdir(parents=True)

		LOGD("Writing SPRD vendor_boot makefiles")
		self._render_template(device_tree_folder, "Android.bp", comment_prefix="//")
		self._render_template(device_tree_folder, "Android.mk")
		self._render_template(device_tree_folder, "sprd_AndroidProducts.mk", out_file="AndroidProducts.mk")
		self._render_template(device_tree_folder, "sprd_BoardConfig.mk", out_file="BoardConfig.mk")
		self._render_template(device_tree_folder, "sprd_device.mk", out_file="device.mk")
		self._render_template(device_tree_folder, "sprd_product.mk",
			out_file=f"twrp_{self.device_info.codename}.mk")
		self._render_template(device_tree_folder, "sprd_system.prop", out_file="system.prop")
		self._render_template(device_tree_folder, "sprd_README.md", out_file="README.md")

		LOGD("Copying vendor_boot DTB and ramdisk payload")
		copyfile(self.vendor_boot.info.dtb, prebuilt_path / "dtb.img")
		self._copy_sprd_vendor_ramdisk(recovery_root_path)
		self._write_sprd_recovery_init(recovery_root_path)
		self._write_sprd_sepolicy_helper(device_tree_folder, prebuilt_path)
		self._write_sprd_twrp_fstab(recovery_root_path)
		self._copy_sprd_source_patches(prebuilt_path / "sourcecode")

		if git:
			self._initialize_git_repo(device_tree_folder)

		return device_tree_folder

	def _copy_sprd_vendor_ramdisk(self, recovery_root_path: Path, stock_root: Path = None):
		"""Keep the factory vendor runtime required before /vendor is mounted."""
		if stock_root is None:
			stock_root = self.vendor_boot.info.ramdisk

		first_stage = stock_root / "first_stage_ramdisk"
		if first_stage.is_dir():
			for source in first_stage.rglob("*"):
				if not source.is_file() or not source.name.startswith("fstab."):
					continue
				destination = recovery_root_path / source.relative_to(stock_root)
				self._copy_fstab_without_avb(source, destination)

		modules = stock_root / "lib" / "modules"
		if modules.is_dir():
			copytree(modules, recovery_root_path / "lib" / "modules")

		for relative_path in ("system/etc/recovery.fstab", "system/etc/ueventd.rc"):
			source = stock_root / relative_path
			if source.is_file():
				destination = recovery_root_path / relative_path
				if source.name.endswith("fstab"):
					self._copy_fstab_without_avb(source, destination)
				else:
					self._copy_file(source, destination)

		# Keep recovery-specific init fragments, all matching uevent rules, and
		# the stock SELinux policy/context set.  Do not copy init.rc: it would
		# replace TWRP's own init entry point in the generated vendor ramdisk.
		for source in stock_root.iterdir():
			if not source.is_file() or not is_required_vendor_ramdisk_root_file(source.name):
				continue
			self._copy_file(source, recovery_root_path / source.name)

		# Vendor boot commonly contains Trusty/KeyMint services and their shared
		# libraries.  They are needed before logical partitions become available,
		# so retain the complete vendor subtree rather than a brittle allow-list.
		vendor = stock_root / "vendor"
		if vendor.is_dir():
			copytree(vendor, recovery_root_path / "vendor")

		for relative_path in (
			"system/etc/vintf/manifest.xml",
			"system/etc/twrp.flags",
			"system/etc/ueventd.rc",
		):
			source = stock_root / relative_path
			if source.is_file():
				self._copy_file(source, recovery_root_path / relative_path)

	def _write_sprd_recovery_init(self, recovery_root_path: Path):
		"""Add a hardware recovery init entry point when stock omitted it."""
		board = self.device_info.bootloader_board_name or ""
		if not fullmatch(r"[A-Za-z0-9_.-]+", board):
			return

		entrypoint = recovery_root_path / f"init.recovery.{board}.rc"
		common = recovery_root_path / "init.recovery.common.rc"
		if not common.is_file():
			return

		custom = recovery_root_path / "init.custom.rc"
		if not custom.exists():
			self._render_template(recovery_root_path, "sprd_init_custom.rc",
				out_file=custom.name)

		if not entrypoint.exists():
			self._render_template(recovery_root_path, "sprd_init_recovery.rc",
				out_file=entrypoint.name)
			return

		# Stock vendor_boot often already has this entrypoint. Preserve all of its
		# services and append our recovery-only hook exactly once.
		contents = entrypoint.read_text(encoding="utf-8")
		if "import /init.custom.rc" not in contents:
			entrypoint.write_text(
				contents.rstrip() + "\n\nimport /init.custom.rc\n",
				encoding="utf-8",
			)

	def _write_sprd_sepolicy_helper(self, device_tree_folder: Path, prebuilt_path: Path):
		"""Package a reproducible stock-policy patch helper when one was extracted."""
		stock_policy = device_tree_folder / "recovery" / "root" / "sepolicy"
		if not stock_policy.is_file():
			return

		copyfile(stock_policy, prebuilt_path / "sepolicy.stock")
		tools_path = device_tree_folder / "tools"
		tools_path.mkdir(parents=True, exist_ok=True)
		self._render_template(tools_path, "sprd_patch_stock_sepolicy.sh",
			out_file="patch_stock_sepolicy.sh")
		self._render_template(tools_path, "sprd_patch_stock_sepolicy.c",
			out_file="patch_stock_sepolicy.c", comment_prefix="//")
		# Android 14.1 vendor_boot trees need a recovery policy overlay in
		# addition to the retained stock binary. It allows TWRP's UI mmap, DRM,
		# input, and dynamic-partition helpers to run before /vendor is mounted.
		if self.sprd_profile.recovery_branch == "twrp-14.1":
			sepolicy_path = device_tree_folder / "sepolicy"
			sepolicy_path.mkdir(parents=True, exist_ok=True)
			self._render_template(sepolicy_path, "sprd_recovery.te",
				out_file="recovery.te")
		mode = S_IRWXU | S_IRGRP | S_IROTH | S_IXGRP | S_IXOTH
		chmod(tools_path / "patch_stock_sepolicy.sh", mode)

	def _write_sprd_twrp_fstab(self, recovery_root_path: Path):
		"""Create the TWRP partition table and expose the vendor_boot slot."""
		contents = self.fstab.format(twrp=True)
		if "/vendor_boot" not in contents:
			contents += (
				"/vendor_boot         emmc      /dev/block/by-name/vendor_boot"
				"   flags=slotselect;backup=1;flashimg=1;display=Vendor Boot\n"
			)
		(recovery_root_path / "system" / "etc").mkdir(parents=True, exist_ok=True)
		(recovery_root_path / "system" / "etc" / "twrp.fstab").write_text(
			contents, encoding="utf-8"
		)

	def _write_sprd_legacy_recovery_fstab(self, recovery_root_path: Path):
		"""Keep a traditional Unisoc recovery fstab at Android's expected path."""
		destination = recovery_root_path / "system" / "etc" / "recovery.fstab"
		destination.parent.mkdir(parents=True, exist_ok=True)
		if self.fstab_source is not None:
			copyfile(self.fstab_source, destination, follow_symlinks=True)
		else:
			destination.write_text(self.fstab.format(twrp=True), encoding="utf-8")

	def _copy_sprd_source_patches(self, sourcecode_path: Path):
		"""Package the source overlay needed to build this generated tree."""
		files_path = sourcecode_path / "files"
		patches = selected_source_patches(self.sprd_profile)

		LOGD("Copying SPRD TWRP source overlay")
		for relative_path in patches:
			source = source_patch_path(self.sprd_profile, relative_path)
			if not source.is_file():
				raise FileNotFoundError(f"Bundled source patch is missing: {source}")
			self._copy_file(source, files_path / relative_path)

		manifest = "# Paths are relative to the TWRP source root.\n"
		manifest += "\n".join(patches) + "\n"
		sourcecode_path.mkdir(parents=True, exist_ok=True)
		(sourcecode_path / "source-files.txt").write_text(manifest, encoding="utf-8")
		self._render_template(sourcecode_path, "sprd_patch.sh", out_file="patch.sh")
		self._render_template(sourcecode_path, "sprd_recovery.sh", out_file="recovery.sh")
		mode = S_IRWXU | S_IRGRP | S_IROTH | S_IXGRP | S_IXOTH
		chmod(sourcecode_path / "patch.sh", mode)
		chmod(sourcecode_path / "recovery.sh", mode)

	def _is_sprd_platform(self) -> bool:
		"""Recognize Unisoc UMS/SC platforms in both boot image layouts."""
		platform = (self.device_info.platform or "").lower()
		return platform.startswith(("ums", "sc986", "sc983", "sp986", "sp7731"))

	@staticmethod
	def _copy_file(source: Path, destination: Path):
		destination.parent.mkdir(parents=True, exist_ok=True)
		copyfile(source, destination, follow_symlinks=True)

	def _copy_fstab_without_avb(self, source: Path, destination: Path):
		# Both supplied trees remove the stock AVB key restrictions so TWRP can
		# mount dynamic partitions after bootloader verification has completed.
		contents = source.read_text(encoding="utf-8")
		contents = sub(r",avb_keys=[^,\s]+", "", contents)
		contents = sub(r",avb(?:=[^,\s]+)?", "", contents)
		destination.parent.mkdir(parents=True, exist_ok=True)
		destination.write_text(contents, encoding="utf-8")

	def _initialize_git_repo(self, device_tree_folder: Path):
		"""Create the optional generated repository with the historic defaults."""
		LOGD("Creating git repo...")

		git_repo = Repo.init(device_tree_folder)
		git_config_reader = git_repo.config_reader()
		git_config_writer = git_repo.config_writer()

		try:
			git_global_email = git_config_reader.get_value('user', 'email')
			git_global_name = git_config_reader.get_value('user', 'name')
		except Exception:
			git_global_email, git_global_name = None, None

		if git_global_email is None or git_global_name is None:
			git_config_writer.set_value('user', 'email', 'barezzisebastiano@gmail.com')
			git_config_writer.set_value('user', 'name', 'Sebastiano Barezzi')

		git_repo.index.add(["*"])
		commit_message = self._render_template(None, "commit_message", to_file=False)
		git_repo.index.commit(commit_message)

	def _render_template(self, *args, comment_prefix: str = "#", **kwargs):
		return render_template(*args,
		                       comment_prefix=comment_prefix,
		                       current_year=self.current_year,
		                       device_info=self.device_info,
		                       fstab=self.fstab,
		                       image_info=self.image_info,
		                       sprd_profile=self.sprd_profile,
		                       legacy_boot=self.legacy_boot,
		                       is_sprd_legacy_recovery=self.is_sprd_legacy_recovery,
		                       vendor_boot=self.vendor_boot.info if self.vendor_boot else None,
		                       version=version,
		                       **kwargs)

	def cleanup(self):
		# Cleanup
		if self.vendor_boot is not None:
			self.vendor_boot.cleanup()
		elif self.aik_manager is not None:
			self.aik_manager.cleanup()
