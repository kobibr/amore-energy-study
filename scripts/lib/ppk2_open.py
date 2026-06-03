"""Shared PPK2 opener that survives a dirty serial buffer.

After a prior start_measuring(), raw streaming bytes can linger in the
serial buffer. The next PPK2_API.get_modifiers() then tries to UTF-8 decode
those bytes and raises UnicodeDecodeError. This helper drains the buffer and
retries with explicit close between attempts.
"""
import time
import serial.tools.list_ports
from ppk2_api.ppk2_api import PPK2_API


def find_ppk2_port(hint=None):
    """Return the PPK2 MEASUREMENT port (lowest ttyACM with VID:PID 1915:c00a).
    fw 1.2.4 exposes a 2nd 'shell' port that does not stream; never pick it."""
    import os
    if hint and os.path.exists(hint):
        return hint
    matches = []
    for p in serial.tools.list_ports.comports():
        try:
            if p.vid == 0x1915 and p.pid == 0xc00a:
                matches.append(p.device)
        except Exception:
            pass
    return sorted(matches)[0] if matches else None


def open_clean(port=None, tries=5, voltage_mv=None, source_meter=False):
    """Open the PPK2, draining any stale streaming bytes before get_modifiers().
    Optionally set source voltage + source-meter mode. Returns a live PPK2_API.
    Raises RuntimeError if it cannot open cleanly after `tries` attempts."""
    port = find_ppk2_port(port)
    if not port:
        raise RuntimeError("no PPK2 measurement port found")
    last = None
    for i in range(tries):
        ppk = None
        try:
            ppk = PPK2_API(port, timeout=2, write_timeout=2)
            # drain any pending raw bytes left by a prior start_measuring()
            try:
                ppk.ser.reset_input_buffer()
                ppk.ser.reset_output_buffer()
                ppk.ser.timeout = 0.3
                while ppk.ser.read(4096):
                    pass
                ppk.ser.timeout = 2
            except Exception:
                pass
            time.sleep(0.3)
            ppk.get_modifiers()
            if voltage_mv is not None:
                ppk.set_source_voltage(int(voltage_mv))
            if source_meter:
                ppk.use_source_meter()
            return ppk
        except Exception as e:
            last = e
            try:
                ppk.ser.close()
            except Exception:
                pass
            time.sleep(1.5)
    raise RuntimeError(f"could not open PPK2 cleanly after {tries} tries: {last}")
