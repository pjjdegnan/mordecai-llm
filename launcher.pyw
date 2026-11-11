"""One-button launcher for the calculator bridge.

Start: closes TI-Nspire software (it holds the USB link), makes sure Ollama is
running (local) or the API key is available (Ollama cloud, Claude), then runs
pc/bridge.py and shows its status. Double-click, or run: pythonw launcher.pyw
"""
import os
import queue
import subprocess
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

from setup_guide import (BRIDGE, HERE, LLM_TNS, NO_WINDOW, OLLAMA_APP, PYTHON, SetupGuide, api_key,
                         has_anthropic_sdk, ollama_up, powershell, ti_software_pids)

# bridge.py console line -> (status text, colour)
STATUSES = [
    ("waiting for the calculator's USB link", "Waiting for the calculator...\nPlug it in and stay on the home screen.", "#b26b00"),
    ("calculator link up", "Ready: open llm on the calculator.", "#1a7f37"),
    ("calculator link lost", "Calculator unplugged. Waiting...", "#b26b00"),
    ("calculator connected", "Connected: chatting.", "#0969da"),
    ("connection closed", "Ready: open llm on the calculator.", "#1a7f37"),
]


class Launcher:
    def __init__(self, root):
        self.root = root
        self.proc = None
        self.chatting = False
        self.guide = None
        self.lines = queue.Queue()

        root.title("Mordecai LLM")
        root.geometry("460x480")
        root.minsize(380, 320)
        frame = ttk.Frame(root, padding=16)
        frame.pack(fill="both", expand=True)

        self.mode = tk.StringVar(value="local")
        modes = ttk.Frame(frame)
        modes.pack(fill="x")
        ttk.Radiobutton(modes, text="Local model (llama3.2:3b)", variable=self.mode, value="local").pack(anchor="w")
        ttk.Radiobutton(modes, text="Cloud model (gpt-oss:120b on ollama.com)", variable=self.mode,
                        value="cloud").pack(anchor="w")
        ttk.Radiobutton(modes, text="Claude (claude-opus-5-5 via the Anthropic API)", variable=self.mode,
                        value="claude").pack(anchor="w")
        ttk.Button(frame, text="Setup guide (first time, or if something isn't working)",
                   command=self.open_guide).pack(fill="x", pady=(10, 0))

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
        if not os.path.exists(LLM_TNS):
            root.after(300, self.open_guide)  # first run

    def open_guide(self):
        if self.guide:
            self.guide.lift()
            self.guide.refresh()
        else:
            self.guide = SetupGuide(self)

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
        mode = self.mode.get()
        threading.Thread(target=self.start, args=(mode, bool(pids)), daemon=True).start()

    def start(self, mode, closed_ti):
        """Runs on a worker thread; UI updates go through self.lines."""
        post = self.lines.put
        env = dict(os.environ)
        if closed_ti:
            time.sleep(2)  # let Windows release the USB device

        if mode == "claude":
            key = api_key("ANTHROPIC_API_KEY")
            if not key:
                post(("idle", "No Claude API key. Create one at platform.claude.com/settings/keys, then run:\n"
                              'setx ANTHROPIC_API_KEY "your-key"'))
                return
            if not has_anthropic_sdk():
                post(("idle", "The Claude SDK isn't installed. Run:\npython -m pip install anthropic"))
                return
            env["ANTHROPIC_API_KEY"] = key
        elif mode == "cloud":
            key = api_key("OLLAMA_API_KEY")
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

        args = [PYTHON, "-u", BRIDGE] + {"cloud": ["--cloud"], "claude": ["--claude"]}.get(mode, [])
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
