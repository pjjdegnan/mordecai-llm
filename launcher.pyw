"""One-button launcher for the calculator bridge.

Start: closes TI-Nspire software (it holds the USB link), makes sure Ollama is
running (local) or the API key is available (cloud), then runs pc/bridge.py and
shows its status. Double-click, or run: pythonw launcher.pyw
"""
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.request
from tkinter import messagebox, ttk

HERE = os.path.dirname(os.path.abspath(__file__))
BRIDGE = os.path.join(HERE, "pc", "bridge.py")
PYTHON = os.path.join(os.path.dirname(sys.executable), "python.exe")
OLLAMA_APP = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama", "ollama app.exe")
NO_WINDOW = subprocess.CREATE_NO_WINDOW

# bridge.py console line -> (status text, colour)
STATUSES = [
    ("waiting for the calculator's USB link", "Waiting for the calculator...\nPlug it in and stay on the home screen.", "#b26b00"),
    ("calculator link up", "Ready: open llm on the calculator.", "#1a7f37"),
    ("calculator link lost", "Calculator unplugged. Waiting...", "#b26b00"),
    ("calculator connected", "Connected: chatting.", "#0969da"),
    ("connection closed", "Ready: open llm on the calculator.", "#1a7f37"),
]


def powershell(cmd):
    r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True,
                       creationflags=NO_WINDOW)
    return r.stdout.strip()


def ti_software_pids():
    out = powershell("Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -like '*TI Education*' } "
                     "| ForEach-Object { $_.ProcessId }")
    return [int(p) for p in out.split()]


def ollama_up():
    try:
        urllib.request.urlopen("http://localhost:11434/api/tags", timeout=2).close()
        return True
    except OSError:
        return False


def api_key():
    key = os.environ.get("OLLAMA_API_KEY")
    if key:
        return key
    # setx stores it for the user; processes started earlier don't see it.
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            return winreg.QueryValueEx(k, "OLLAMA_API_KEY")[0]
    except OSError:
        return None


class Launcher:
    def __init__(self, root):
        self.root = root
        self.proc = None
        self.chatting = False
        self.lines = queue.Queue()

        root.title("Mordecai LLM")
        root.geometry("460x420")
        root.minsize(380, 320)
        frame = ttk.Frame(root, padding=16)
        frame.pack(fill="both", expand=True)

        self.mode = tk.StringVar(value="local")
        modes = ttk.Frame(frame)
        modes.pack(fill="x")
        ttk.Radiobutton(modes, text="Local model (llama3.2:3b)", variable=self.mode, value="local").pack(anchor="w")
        ttk.Radiobutton(modes, text="Cloud model (gpt-oss:120b on ollama.com)", variable=self.mode,
                        value="cloud").pack(anchor="w")

        self.button = tk.Button(frame, text="Start", font=("Segoe UI", 16, "bold"), bg="#1a7f37", fg="white",
                                activebackground="#116329", activeforeground="white", relief="flat",
                                command=self.toggle)
        self.button.pack(fill="x", pady=(12, 8), ipady=8)

        self.status = tk.Label(frame, text="Stopped.", font=("Segoe UI", 12), justify="left", anchor="w",
                               wraplength=420)
        self.status.pack(fill="x")
        ttk.Label(frame, foreground="#57606a", wraplength=420, justify="left",
                  text="If the calculator restarted, open ndless/ndless_installer on it first. "
                       "Quit llm (esc) before pressing Stop.").pack(fill="x", pady=(4, 8))

        self.log = tk.Text(frame, height=10, font=("Consolas", 9), state="disabled", wrap="word")
        self.log.pack(fill="both", expand=True)

        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(200, self.poll)

    def say(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text.rstrip() + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def set_status(self, text, colour="#24292f"):
        self.status.configure(text=text, fg=colour)

    def toggle(self):
        if self.proc:
            self.stop()
            return
        self.button.configure(state="disabled")
        self.set_status("Checking...")
        self.root.update_idletasks()
        pids = ti_software_pids()
        if pids:
            if not messagebox.askyesno("Mordecai LLM", "TI-Nspire software is open and holds the USB link.\n\n"
                                       "Close it now?"):
                self.set_status("Stopped: close TI-Nspire software first.", "#cf222e")
                self.button.configure(state="normal")
                return
            powershell("Stop-Process -Force -Id " + ",".join(map(str, pids)))
            self.say("closed TI-Nspire software")
        cloud = self.mode.get() == "cloud"
        threading.Thread(target=self.start, args=(cloud, bool(pids)), daemon=True).start()

    def start(self, cloud, closed_ti):
        """Runs on a worker thread; UI updates go through self.lines."""
        post = self.lines.put
        env = dict(os.environ)
        if closed_ti:
            time.sleep(2)  # let Windows release the USB device

        if cloud:
            key = api_key()
            if not key:
                post(("idle", "No API key. Create one at ollama.com/settings/keys, then run:\n"
                              'setx OLLAMA_API_KEY "your-key"'))
                return
            env["OLLAMA_API_KEY"] = key
        elif not ollama_up():
            if not os.path.exists(OLLAMA_APP):
                post(("idle", "Ollama isn't installed. Get it from ollama.com/download."))
                return
            post(("status", ("Starting Ollama...", "#b26b00")))
            subprocess.Popen([OLLAMA_APP], creationflags=NO_WINDOW)
            for _ in range(30):
                if ollama_up():
                    break
                time.sleep(1)
            else:
                post(("idle", "Ollama didn't start. Open it from the Start menu and try again."))
                return

        args = [PYTHON, "-u", BRIDGE] + (["--cloud"] if cloud else [])
        self.proc = subprocess.Popen(args, cwd=HERE, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
        post(("running", None))
        for line in self.proc.stdout:
            post(("line", line))
        code = self.proc.wait()
        post(("exited", code))

    def stop(self):
        """Returns False if the user cancelled."""
        if self.chatting and not messagebox.askyesno(
                "Mordecai LLM", "The calculator is still connected. Stopping now can freeze it.\n\n"
                                "Press esc on the calculator first. Stop anyway?"):
            return False
        if self.proc:
            self.proc.terminate()
        return True

    def poll(self):
        while not self.lines.empty():
            kind, value = self.lines.get()
            if kind == "log":
                self.say(value)
            elif kind == "status":
                self.set_status(*value)
            elif kind == "idle":
                self.set_status(value, "#cf222e")
                self.button.configure(state="normal")
            elif kind == "running":
                self.button.configure(state="normal", text="Stop", bg="#cf222e", activebackground="#a40e26")
                self.set_status("Starting bridge...", "#b26b00")
            elif kind == "line":
                self.say(value)
                for needle, text, colour in STATUSES:
                    if needle in value:
                        self.set_status(text, colour)
                        self.chatting = needle == "calculator connected"
            elif kind == "exited":
                self.proc = None
                self.chatting = False
                self.button.configure(state="normal", text="Start", bg="#1a7f37", activebackground="#116329")
                self.set_status("Stopped." if value in (0, 1) else f"Bridge stopped unexpectedly (code {value}). "
                                "See the log above.", "#24292f" if value in (0, 1) else "#cf222e")
        self.root.after(200, self.poll)

    def close(self):
        if self.proc and not self.stop():
            return
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    Launcher(root)
    root.mainloop()
