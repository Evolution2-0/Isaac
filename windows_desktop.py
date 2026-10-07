from __future__ import annotations

import asyncio
import base64
from pathlib import Path

async def powershell(script: str, timeout: float = 20.0) -> dict:
    proc = await asyncio.create_subprocess_exec(
        "powershell.exe", "-NoProfile", "-NonInteractive",
        "-ExecutionPolicy", "Bypass", "-Command", script,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        return {"ok": False, "error": f"PowerShell timeout ({int(timeout)}s)"}
    return {
        "ok": proc.returncode == 0,
        "stdout": (stdout or b"").decode(errors="replace").strip(),
        "stderr": (stderr or b"").decode(errors="replace").strip(),
        "exit_code": proc.returncode,
    }

def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")

async def screenshot(path: str) -> dict:
    safe = path.replace("'", "''")
    script = f"""
Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Windows.Forms
$bounds = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
$bmp = New-Object System.Drawing.Bitmap $bounds.Width, $bounds.Height
$gfx = [System.Drawing.Graphics]::FromImage($bmp)
$gfx.CopyFromScreen($bounds.Location, [System.Drawing.Point]::Empty, $bounds.Size)
$bmp.Save('{safe}', [System.Drawing.Imaging.ImageFormat]::Png)
$gfx.Dispose()
$bmp.Dispose()
"""
    result = await powershell(script)
    if not result["ok"]:
        return {"ok": False, "error": result.get("stderr") or result.get("error") or "Screenshot fehlgeschlagen"}
    return {"ok": True, "path": path, "bytes": Path(path).stat().st_size if Path(path).exists() else 0, "via": "windows"}

async def tap(x: int, y: int) -> dict:
    script = f"""
Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class IsaacMouse {{
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int X, int Y);
  [DllImport("user32.dll")] public static extern void mouse_event(uint flags, uint dx, uint dy, uint data, UIntPtr extra);
}}
"@
[IsaacMouse]::SetCursorPos({x},{y}) | Out-Null
[IsaacMouse]::mouse_event(0x0002,0,0,0,[UIntPtr]::Zero)
[IsaacMouse]::mouse_event(0x0004,0,0,0,[UIntPtr]::Zero)
"""
    result = await powershell(script, 10)
    return {"ok": result["ok"], "x": x, "y": y, "via": "windows_user32", "error": result.get("stderr") if not result["ok"] else ""}

async def swipe(x1: int, y1: int, x2: int, y2: int, ms: int = 300) -> dict:
    ms = max(1, min(int(ms), 5000))
    script = f"""
Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class IsaacMouse {{
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int X, int Y);
  [DllImport("user32.dll")] public static extern void mouse_event(uint flags, uint dx, uint dy, uint data, UIntPtr extra);
}}
"@
[IsaacMouse]::SetCursorPos({x1},{y1}) | Out-Null
[IsaacMouse]::mouse_event(0x0002,0,0,0,[UIntPtr]::Zero)
Start-Sleep -Milliseconds {ms}
[IsaacMouse]::SetCursorPos({x2},{y2}) | Out-Null
[IsaacMouse]::mouse_event(0x0004,0,0,0,[UIntPtr]::Zero)
"""
    result = await powershell(script, 10)
    return {"ok": result["ok"], "x1":x1,"y1":y1,"x2":x2,"y2":y2,"ms":ms,"via":"windows_user32","error":result.get("stderr") if not result["ok"] else ""}

async def clipboard_get() -> dict:
    result = await powershell("Get-Clipboard -Raw", 10)
    return {"ok": result["ok"], "text": result.get("stdout","")[:4000], "via":"windows_powershell", "error":result.get("stderr") if not result["ok"] else ""}

async def clipboard_set(text: str) -> dict:
    encoded = _b64(text)
    script = f"$b=[Convert]::FromBase64String('{encoded}'); [Text.Encoding]::UTF8.GetString($b) | Set-Clipboard"
    result = await powershell(script, 10)
    return {"ok": result["ok"], "length":len(text), "via":"windows_powershell", "error":result.get("stderr") if not result["ok"] else ""}

async def type_text(text: str) -> dict:
    encoded = _b64(text)
    script = f"""
Add-Type -AssemblyName System.Windows.Forms
$b=[Convert]::FromBase64String('{encoded}')
[Text.Encoding]::UTF8.GetString($b) | Set-Clipboard
Start-Sleep -Milliseconds 100
[System.Windows.Forms.SendKeys]::SendWait('^v')
"""
    result = await powershell(script, 15)
    return {"ok": result["ok"], "length":len(text), "via":"windows_sendkeys_clipboard", "error":result.get("stderr") if not result["ok"] else ""}

async def open_target(target: str) -> dict:
    encoded = _b64(target)
    script = f"$b=[Convert]::FromBase64String('{encoded}'); $t=[Text.Encoding]::UTF8.GetString($b); Start-Process $t"
    result = await powershell(script, 15)
    return {"ok": result["ok"], "opened":target, "via":"windows_powershell", "error":result.get("stderr") if not result["ok"] else ""}

async def key(code: str) -> dict:
    mapping = {"enter":"{ENTER}","esc":"{ESC}","escape":"{ESC}","tab":"{TAB}","space":" ","backspace":"{BACKSPACE}","delete":"{DELETE}","up":"{UP}","down":"{DOWN}","left":"{LEFT}","right":"{RIGHT}","home":"{HOME}","end":"{END}","pgup":"{PGUP}","pgdn":"{PGDN}","ctrl+c":"^c","ctrl+v":"^v","ctrl+l":"^l","alt+f4":"%{F4}"}
    send = mapping.get(code.strip().lower(), code)
    encoded = _b64(send)
    script = f"$b=[Convert]::FromBase64String('{encoded}'); $k=[Text.Encoding]::UTF8.GetString($b); Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.SendKeys]::SendWait($k)"
    result = await powershell(script, 10)
    return {"ok":result["ok"],"code":code,"via":"windows_sendkeys","error":result.get("stderr") if not result["ok"] else ""}
