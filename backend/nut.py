import subprocess
import re


def upsc(ups_name: str) -> dict:
    try:
        result = subprocess.run(
            ["upsc", f"{ups_name}@localhost"],
            capture_output=True, text=True, timeout=5
        )
        data = {}
        for line in result.stdout.splitlines():
            if ": " in line:
                key, _, val = line.partition(": ")
                data[key.strip()] = val.strip()
        return data
    except Exception:
        return {}


def upsc_list() -> list:
    try:
        result = subprocess.run(
            ["upsc", "-l", "localhost"],
            capture_output=True, text=True, timeout=5
        )
        return [l.strip() for l in result.stdout.splitlines() if l.strip()]
    except Exception:
        return []


def scan_usb() -> list:
    """Сканирование USB через nut-scanner"""
    try:
        result = subprocess.run(
            ["nut-scanner", "-U"],
            capture_output=True, text=True, timeout=15
        )
        devices = []
        current = {}
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith("[nutdev"):
                if current:
                    devices.append(current)
                current = {}
            elif "=" in line:
                k, _, v = line.partition("=")
                current[k.strip()] = v.strip().strip('"')
        if current:
            devices.append(current)
        return devices
    except Exception:
        return []


def get_ups_info(ups_name: str) -> dict:
    data = upsc(ups_name)
    if not data:
        return {"name": ups_name, "online": False, "status": "UNKNOWN"}
    return {
        "name": ups_name,
        "online": True,
        "status": data.get("ups.status", "UNKNOWN"),
        "charge": _float(data.get("battery.charge")),
        "charge_low": _float(data.get("battery.charge.low")),
        "runtime": _int(data.get("battery.runtime")),
        "load": _float(data.get("ups.load")),
        "input_voltage": _float(data.get("input.voltage")),
        "output_voltage": _float(data.get("output.voltage")),
        "input_freq": _float(data.get("input.frequency")),
        "output_freq": _float(data.get("output.frequency")),
        "battery_voltage": _float(data.get("battery.voltage")),
        "battery_voltage_nominal": _float(data.get("battery.voltage.nominal")),
        "temperature": _float(data.get("battery.temperature")),
        "model": data.get("device.model") or data.get("ups.model", ups_name),
        "mfr": data.get("device.mfr") or data.get("ups.mfr", ""),
        "serial": data.get("device.serial") or data.get("ups.serial", ""),
        "beeper": data.get("ups.beeper.status", ""),
        "test_result": data.get("ups.test.result", ""),
        "raw": data,
    }


def run_command(ups_name: str, command: str, admin_user: str, admin_pass: str) -> dict:
    allowed = {
        "test.battery.start",
        "test.battery.stop",
        "test.battery.start.quick",
        "beeper.toggle",
        "beeper.enable",
        "beeper.disable",
        "load.off",
        "load.on",
    }
    if command not in allowed:
        return {"ok": False, "error": "Команда не разрешена"}
    try:
        result = subprocess.run(
            ["upscmd", "-u", admin_user, "-p", admin_pass, ups_name, command],
            capture_output=True, text=True, timeout=10
        )
        out = result.stdout.strip() or result.stderr.strip()
        return {"ok": result.returncode == 0, "result": out}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def list_commands(ups_name: str, admin_user: str, admin_pass: str) -> list:
    try:
        result = subprocess.run(
            ["upscmd", "-u", admin_user, "-p", admin_pass, "-l", ups_name],
            capture_output=True, text=True, timeout=5
        )
        cmds = []
        for line in result.stdout.splitlines():
            if " - " in line:
                name, _, desc = line.partition(" - ")
                cmds.append({"name": name.strip(), "desc": desc.strip()})
        return cmds
    except Exception:
        return []


def write_ups_conf(ups_list: list):
    """Генерация /etc/nut/ups.conf из списка UPS"""
    lines = []
    for ups in ups_list:
        lines.append(f"[{ups['name']}]")
        lines.append(f"    driver = {ups.get('driver', 'usbhid-ups')}")
        lines.append(f"    port = {ups.get('port', 'auto')}")
        if ups.get("vendorid"):
            lines.append(f"    vendorid = {ups['vendorid']}")
        if ups.get("productid"):
            lines.append(f"    productid = {ups['productid']}")
        if ups.get("serial"):
            lines.append(f'    serial = "{ups["serial"]}"')
        if ups.get("description"):
            lines.append(f'    desc = "{ups["description"]}"')
        lines.append("")
    content = "\n".join(lines)
    with open("/etc/nut/ups.conf", "w") as f:
        f.write(content)
    return content


def write_upsd_users(nut_users: list):
    """Генерация /etc/nut/upsd.users"""
    lines = []
    for u in nut_users:
        lines.append(f"[{u['username']}]")
        lines.append(f"    password = {u['password']}")
        if u.get("actions"):
            lines.append(f"    actions = {u['actions']}")
        if u.get("instcmds"):
            lines.append(f"    instcmds = {u['instcmds']}")
        lines.append(f"    upsmon {u['role']}")
        lines.append("")
    content = "\n".join(lines)
    with open("/etc/nut/upsd.users", "w") as f:
        f.write(content)
    return content


def restart_nut():
    try:
        subprocess.run(["upsdrvctl", "stop"], timeout=10)
        subprocess.run(["upsdrvctl", "start"], timeout=15)
        subprocess.run(["systemctl", "restart", "nut-server"], timeout=10)
        subprocess.run(["systemctl", "restart", "nut-monitor"], timeout=10)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def get_nut_status():
    services = {}
    for svc in ["nut-server", "nut-monitor"]:
        try:
            r = subprocess.run(
                ["systemctl", "is-active", svc],
                capture_output=True, text=True, timeout=5
            )
            services[svc] = r.stdout.strip()
        except Exception:
            services[svc] = "unknown"
    return services


def _float(val):
    try:
        return float(val)
    except Exception:
        return None


def _int(val):
    try:
        return int(val)
    except Exception:
        return None
