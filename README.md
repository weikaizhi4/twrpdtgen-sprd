# twrpdtgen-sprd

[![PyPi version](https://img.shields.io/pypi/v/twrpdtgen)](https://pypi.org/project/twrpdtgen/)
[![Codacy Badge](https://app.codacy.com/project/badge/Grade/ae7d7a75522b4d079c497ff6d9e052d1)](https://www.codacy.com/gh/twrpdtgen/twrpdtgen/dashboard?utm_source=github.com&amp;utm_medium=referral&amp;utm_content=twrpdtgen/twrpdtgen&amp;utm_campaign=Badge_Grade)

Create a [TWRP](https://twrp.me/)-compatible device tree only from an Android recovery image (or a boot image if the device uses non-dynamic partitions A/B) of your device's stock ROM.
The generic path has been confirmed for Android 4.4 through Android 16.

## Unisoc vendor_boot

This fork also recognizes Android `vendor_boot` v3/v4 images and generates a
Unisoc/SPRD TWRP tree. It extracts the vendor ramdisk, DTB/DTBO, vendor command
line, ramdisk table, and bootconfig directly from the input image.

- All generated Unisoc/SPRD products use `twrp_<codename>`, never
  `omni_<codename>`, including traditional `boot.img` trees.
- `BoardConfig.mk` includes `BOARD_USES_SPRD_HARDWARE := true` and the stock
  vendor_boot parameters.
- `prop.default` is read from the stock ramdisk. Android 14 and newer selects
  `twrp-14.1` with the `-ap2a` lunch variant; Android 13 and earlier selects
  `twrp-12.1`.
- The generated vendor ramdisk keeps the stock DTB, first-stage fstab, kernel
  modules, recovery init/ueventd files, SELinux policy/context files, and the
  complete vendor runtime (including Trusty/KeyMint) alongside a TWRP fstab
  with a slot-aware `vendor_boot` entry. The stock `init.rc` is deliberately
  excluded so it cannot replace TWRP's init entry point.
- When the vendor ramdisk contains a stock SELinux policy, the tree also
  packages `prebuilt/sepolicy.stock`, `tools/patch_stock_sepolicy.sh`, and a
  small `libsepol` host helper source. The SPRD builder compiles and runs it
  automatically. For a local build, compile `tools/patch_stock_sepolicy.c`
  with `-Wl,-Bstatic -lsepol -Wl,-Bdynamic`, then set
  `SEPOLICY_PATCHER` to that executable before running the shell helper.

The generated README records the selected source branch and exact lunch target.
When a factory image exposes a generic Unisoc identity rather than the device
codename, provide the intended tree location explicitly:

```sh
python3 -m twrpdtgen your_vendor_boot.img \
    --manufacturer Manufacturer --codename Codename
```

Traditional Android `boot.img`/`recovery.img` images from UMS/SC platforms are
supported as well. For v0-v2 images the generator reads the boot header
directly, preserving the kernel/ramdisk/tags/DTB offsets, page size, recovery
DTBO metadata, and command line. The stock fstab is installed at
`recovery/root/system/etc/recovery.fstab`, which matches the Android 11
traditional recovery layout. Android 13 and earlier use the `twrp-12.1` profile, while Android 14 and
newer use `twrp-14.1` with the `-ap2a` lunch variant. `twrp-12.1` keeps the
original bundled source overlay; `twrp-14.1` uses its separate TWRP 14.1
overlay. UMS platforms receive the legacy DRM build hook, and SC27XX vibrator
support is enabled only when the vendor ramdisk contains its driver. Both
profiles retain and patch a stock SELinux policy whenever the input ramdisk
supplies one; there is no Android-version-based policy split.
Panel dimensions are read from the stock DTB first and then from every FDT
entry in a DTBO image. This covers traditional UMS512 devices whose touch
display coordinates exist only in a later DTBO overlay.

Requires Python 3.8 or greater

## Installation

For this Unisoc vendor_boot fork, unpack the archive and install the local
project rather than the unmodified PyPI release:

```sh
python3 -m pip install .
```

Then run the installed command, or use `python3 -m twrpdtgen` from the project
directory:

```sh
twrpdtgen /path/to/vendor_boot.img -o output
```

If the stock properties expose a generic Unisoc codename, supply the intended
device-tree identity:

```sh
twrpdtgen /path/to/vendor_boot.img -o output \
    --manufacturer Manufacturer --codename Codename
```

The generated `README.md` identifies the required TWRP source branch and
lunch target. Use Linux or WSL with `cpio` installed; LZ4-compressed ramdisks
also require `lz4`.

The upstream release remains available with:

```sh
pip3 install twrpdtgen
```

## Instructions

```sh
python3 -m twrpdtgen <path to image>
```

When an image is provided, if everything goes well, there will be a device tree at `output/manufacturer/codename`

You can also use the module in a script, with the following code:

```python
from twrpdtgen.device_tree import DeviceTree

# Get image info
device_tree = DeviceTree(image_path)

# Dump device tree to folder
device_tree.dump_to_folder(output_path)
```

## License

```
#
# Copyright (C) 2022 The Android Open Source Project
#
# SPDX-License-Identifier: Apache-2.0
#
```
