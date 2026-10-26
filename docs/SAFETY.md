# ZxmWare Safety Notes

Plain-language safety and recovery notes for flashing custom firmware and apps
onto a Flipper Zero with ZxmWare. Read this once before your first firmware
install. It is meant to be factual, not alarmist — most installs go fine, and a
Flipper is hard to permanently kill.

---

## Before you flash firmware

- **Custom firmware may void your warranty.** Running non-official firmware is
  generally an at-your-own-risk activity as far as the manufacturer is
  concerned. Apps (`.fap`) are lower risk than firmware; they just copy to the
  SD card and don't touch the bootloader.
- **Keep the battery charged.** Flash with the device plugged in and at a
  healthy charge (50%+ is a good rule). A firmware update rewrites internal
  flash and reboots into an updater; losing power partway through is the main
  way to end up needing recovery.
- **Don't interrupt an update.** Once a firmware install starts, leave the cable
  connected and don't press buttons until the device finishes rebooting. The web
  installer shows a "do not disconnect" warning for exactly this reason.
- **Use the supported tools for firmware.** The companion CLI
  (`installer/zxmware.py`) verifies the bundle's sha256 before flashing, and
  **qFlipper** (the official desktop app) uses the native USB protocol and is
  the supported recovery/flashing tool. The Web Serial firmware button is
  **experimental** — fine for experimenting, not what you reach for when it
  matters.

---

## Don't edit the update manifest

The OTA bundle's `update.fuf` carries integrity and hardware-safety values that
the packer (`build/pack_update.py`) computes for you:

- **Never edit the CRC values** (`Firmware CRC`, `Radio CRC`, `Loader CRC`). They
  are checksums of the images; a mismatch means the updater refuses or
  mis-applies the image.
- **Never edit the OB (option-byte) values** (`OB reference`, `OB mask`,
  `OB write mask`). Option bytes control low-level chip settings such as read
  protection; wrong values can lock or brick the microcontroller.
- Leave `Target` at **7** (the production stm32wb Flipper). Installing a bundle
  built for the wrong target is a recipe for a failed flash.

If you need a different build, re-run the packer rather than editing the
manifest.

---

## Your legal responsibility

The Flipper Zero can transmit on sub-GHz radio, NFC, RFID, infrared, and more.
**You are responsible for complying with the laws and regulations where you
are** regarding what you transmit, on which frequencies, and at what power.
Radio rules vary by country/region. Only use the device on frequencies and in
ways that are legal for you, and never to interfere with others' devices or
communications. ZxmWare distributes software; how you use the hardware is on you.

---

## Recovering a bricked or stuck device

A Flipper that won't boot normally is almost always recoverable — the stm32
bootloader lives in ROM and can't be overwritten. **qFlipper is the supported
recovery tool.**

1. **Try a normal recovery first.** Install qFlipper, connect the Flipper by
   USB, and use its **Repair / Recover** option. qFlipper can usually re-flash a
   healthy firmware over a bad one without any manual steps.

2. **If the device is fully unresponsive, enter DFU mode** (the chip's built-in
   USB bootloader):
   - Hold **BACK + LEFT** together for ~5 seconds, then release **BACK** while
     keeping **LEFT** held until the blue LED blinks — the screen may stay blank.
     The device is now in DFU.
   - With the Flipper in DFU and connected by USB, qFlipper will detect it and
     offer to flash a full firmware image. Let it complete without unplugging.

3. **After recovery,** boot normally, then re-run your install through the
   supported path (CLI or qFlipper). If a specific ZxmWare build caused the
   problem, flash the previous known-good release before retrying.

If qFlipper doesn't see the device at all, try a different USB cable/port (some
cables are charge-only), and make sure nothing else is holding the serial port
(close the web installer tab and the CLI). A device that charges but never
appears over USB in any mode is the rare case worth contacting support about.

---

## Quick checklist

- [ ] Battery charged, cable connected, won't be disturbed.
- [ ] Using CLI or qFlipper for firmware (web firmware button is experimental).
- [ ] Didn't hand-edit `update.fuf` CRC/OB values.
- [ ] Transmitting only what's legal where I am.
- [ ] qFlipper installed, so I can recover if needed.
