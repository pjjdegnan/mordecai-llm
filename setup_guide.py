"""Step-by-step setup window for the launcher, plus helpers both windows share.

Each step checks itself where it can (TI software, calculator on USB, downloaded
files, the chosen AI backend) and offers buttons for what can be automated.
Steps that happen on the calculator are explained in place.
"""
import ctypes
import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
import urllib.request
import webbrowser
import zipfile
from tkinter import messagebox, simpledialog, ttk

HERE = os.path.dirname(os.path.abspath(__file__))
BRIDGE = os.path.join(HERE, "pc", "bridge.py")
PYTHON = os.path.join(os.path.dirname(sys.executable), "python.exe")
OLLAMA_DIR = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama")
OLLAMA_APP = os.path.join(OLLAMA_DIR, "ollama app.exe")
OLLAMA_EXE = os.path.join(OLLAMA_DIR, "ollama.exe")
LOCAL_MODEL = "llama3.2:3b"
NO_WINDOW = subprocess.CREATE_NO_WINDOW

NDLESS_DIR = os.path.join(HERE, "ndless-release")
NDLESS_TAG = "r2022"
NDLESS_FILES = ["ndless_resources.tns", "ndless_installer_4.5.5-6.2.0-6.4.0.tns"]
LLM_TNS = os.path.join(HERE, "calc", "llm.tns")
LLM_REPO = "pjjdegnan/mordecai-llm"

OK, MISSING, MANUAL = "ok", "missing", "manual"
MARKS = {OK: ("✔", "#1a7f37"), MISSING: ("✖", "#cf222e"), MANUAL: ("●", "#57606a")}


# --- helpers shared with launcher.pyw -------------------------------------------

def powershell(cmd):
    r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True,
                       creationflags=NO_WINDOW)
    return r.stdout.strip()


def ti_software_pids():
    out = powershell("Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -like '*TI Education*' } "
                     "| ForEach-Object { $_.ProcessId }")
    return [int(p) for p in out.split()]


def ollama_models():
    """Names of pulled models, or None if Ollama isn't running."""
    try:
        with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=2) as r:
            return [m["name"] for m in json.load(r)["models"]]
    except OSError:
        return None


def ollama_up():
    return ollama_models() is not None


def api_key(name):
    key = os.environ.get(name)
    if key:
        return key
    # setx stores it for the user; processes started earlier don't see it.
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            return winreg.QueryValueEx(k, name)[0]
    except OSError:
        return None


def save_api_key(name, value):
    """Same effect as `setx NAME value`, without putting the key on a command line."""
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)
    os.environ[name] = value
    # Tell Explorer so terminals opened from now on see it.
    HWND_BROADCAST, WM_SETTINGCHANGE, SMTO_ABORTIFHUNG = 0xFFFF, 0x1A, 0x2
    ctypes.windll.user32.SendMessageTimeoutW(HWND_BROADCAST, WM_SETTINGCHANGE, 0, "Environment",
                                             SMTO_ABORTIFHUNG, 2000, None)


def has_anthropic_sdk():
    r = subprocess.run([PYTHON, "-c", "import anthropic"], capture_output=True, creationflags=NO_WINDOW)
    return r.returncode == 0


def ti_link_library():
    """Folder with TI's navnet.dll, or None."""
    pc = os.path.join(HERE, "pc")
    if pc not in sys.path:
        sys.path.insert(0, pc)
    import navnet
    try:
        return navnet._find_lib_dir()
    except navnet.NavNetError:
        return None


def calculator_on_usb():
    return powershell("Get-PnpDevice -PresentOnly | Where-Object { $_.InstanceId -match 'VID_0451' } "
                      "| Select-Object -First 1 -ExpandProperty FriendlyName")


def github_asset(api_path, name_test):
    req = urllib.request.Request("https://api.github.com/repos/" + api_path,
                                 headers={"User-Agent": "mordecai-llm-setup", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        assets = json.load(r)["assets"]
    for a in assets:
        if name_test(a["name"]):
            return a["browser_download_url"]
    raise RuntimeError("release file not found")


def download(url, path):
    req = urllib.request.Request(url, headers={"User-Agent": "mordecai-llm-setup"})
    with urllib.request.urlopen(req, timeout=120) as r, open(path, "wb") as f:
        f.write(r.read())


# --- the guide window -----------------------------------------------------------

class SetupGuide(tk.Toplevel):
    def __init__(self, launcher):
        super().__init__(launcher.root)
        self.launcher = launcher
        self.events = queue.Queue()
        self.busy = False

        self.title("Mordecai LLM - Setup guide")
        self.geometry("640x720")
        self.minsize(520, 560)
        outer = ttk.Frame(self, padding=14)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Work down the list. Green ticks are done; buttons do the step for you.",
                  wraplength=600).pack(fill="x")

        self.steps = ttk.Frame(outer)
        self.steps.pack(fill="x", pady=(10, 6))
        self.steps.columnconfigure(1, weight=1)

        bar = ttk.Frame(outer)
        bar.pack(fill="x")
        self.recheck = ttk.Button(bar, text="Re-check", command=self.refresh)
        self.recheck.pack(side="left")
        self.activity = ttk.Label(bar, foreground="#57606a")
        self.activity.pack(side="left", padx=10)

        self.log = tk.Text(outer, height=8, font=("Consolas", 9), state="disabled", wrap="word")
        self.log.pack(fill="both", expand=True, pady=(8, 0))

        self.mode_trace = launcher.mode.trace_add("write", lambda *_: self.refresh())
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.after(200, self.poll)
        self.refresh()

    def close(self):
        self.launcher.mode.trace_remove("write", self.mode_trace)
        self.launcher.guide = None
        self.destroy()

    # Checks run on a worker thread (PowerShell is slow); results come back via self.events.

    def refresh(self):
        self.activity.configure(text="Checking...")
        mode = self.launcher.mode.get()
        threading.Thread(target=lambda: self.events.put(("steps", self.check_all(mode))), daemon=True).start()

    def check_all(self, mode):
        steps = []
        lib = ti_link_library()
        steps.append(("TI-Nspire Student Software",
                      (OK, f"Link library found in {lib}") if lib else
                      (MISSING, "Install TI-Nspire CX (CAS) Student or Teacher Software from education.ti.com. "
                                "The bridge uses its link library and USB driver."),
                      [] if lib else [("Open TI website", lambda: webbrowser.open("https://education.ti.com"))]))

        calc = calculator_on_usb()
        steps.append(("Calculator plugged in",
                      (OK, calc) if calc else
                      (MISSING, "Connect the calculator to this PC with its USB cable and turn it on."), []))

        steps.append(("Calculator OS is 6.2.0.333 or 6.4.0.74",
                      (MANUAL, "Check on the calculator: Home > 5 Settings > 4 Status. Ndless only runs on "
                               "these versions, and TI doesn't allow downgrading."), []))

        have_ndless = all(os.path.exists(os.path.join(NDLESS_DIR, f)) for f in NDLESS_FILES)
        steps.append(("Ndless downloaded",
                      (OK, f"Ndless {NDLESS_TAG} is in ndless-release/") if have_ndless else
                      (MISSING, f"Ndless {NDLESS_TAG} lets the calculator run llm."),
                      [] if have_ndless else [("Download Ndless", self.download_ndless)]))

        steps.append(("Ndless installed on the calculator",
                      (MANUAL, "1. Send the files (button). 2. On the calculator open "
                               "ndless/ndless_installer_4.5.5-6.2.0-6.4.0 and wait for 'Ndless installed'. "
                               "Repeat the open step after every calculator restart or reset."),
                      [("Send Ndless to calculator",
                        lambda: self.send([os.path.join(NDLESS_DIR, f) for f in NDLESS_FILES], "/ndless"))]
                      if have_ndless else []))

        have_llm = os.path.exists(LLM_TNS)
        steps.append(("llm program on the calculator",
                      (OK if have_llm else MISSING,
                       "Send llm.tns to the calculator (button). It goes into a folder called llm." if have_llm else
                       "Download llm.tns from this project's GitHub release first."),
                      [("Send llm to calculator", lambda: self.send([LLM_TNS], "/llm"))] if have_llm else
                      [("Download llm.tns", self.download_llm)]))

        steps.append(self.check_backend(mode))

        steps.append(("Start",
                      (MANUAL, "Close this guide. With the calculator on its home screen, press Start, wait for "
                               "'Ready', then open llm > llm on the calculator. Press esc on the calculator "
                               "before pressing Stop."), []))
        return steps

    def check_backend(self, mode):
        if mode == "claude":
            sdk, key = has_anthropic_sdk(), api_key("ANTHROPIC_API_KEY")
            actions = []
            if not sdk:
                actions.append(("Install Claude SDK", lambda: self.run_job(
                    "Installing the anthropic package", [PYTHON, "-m", "pip", "install", "anthropic"])))
            if not key:
                actions += [("Get a key", lambda: webbrowser.open("https://platform.claude.com/settings/keys")),
                            ("Enter key", lambda: self.enter_key("ANTHROPIC_API_KEY", "Claude API key"))]
            missing = [n for n, ok in (("the anthropic package", sdk), ("an API key", key)) if not ok]
            return ("AI backend: Claude",
                    (OK, "SDK installed and API key saved. API use is billed per token by Anthropic.")
                    if not missing else (MISSING, "Needs " + " and ".join(missing) + "."), actions)
        if mode == "cloud":
            key = api_key("OLLAMA_API_KEY")
            return ("AI backend: Ollama cloud",
                    (OK, "API key saved.") if key else (MISSING, "Needs an ollama.com API key."),
                    [] if key else [("Get a key", lambda: webbrowser.open("https://ollama.com/settings/keys")),
                                    ("Enter key", lambda: self.enter_key("OLLAMA_API_KEY", "ollama.com API key"))])
        if not os.path.exists(OLLAMA_EXE):
            return ("AI backend: local model", (MISSING, "Install Ollama, then come back and pull the model."),
                    [("Get Ollama", lambda: webbrowser.open("https://ollama.com/download"))])
        models = ollama_models()
        if models is None:
            return ("AI backend: local model", (MISSING, "Ollama is installed but not running."),
                    [("Start Ollama", lambda: (subprocess.Popen([OLLAMA_APP], creationflags=NO_WINDOW),
                                               self.after(5000, self.refresh)))])
        if not any(m == LOCAL_MODEL or m.startswith(LOCAL_MODEL + ":") for m in models):
            return ("AI backend: local model", (MISSING, f"Ollama is running; the {LOCAL_MODEL} model (about 2 GB) "
                                                         "isn't downloaded yet."),
                    [(f"Download {LOCAL_MODEL}", lambda: self.run_job(
                        f"Downloading {LOCAL_MODEL}", [OLLAMA_EXE, "pull", LOCAL_MODEL]))])
        return ("AI backend: local model", (OK, f"Ollama is running with {LOCAL_MODEL}."), [])

    def show_steps(self, steps):
        for w in self.steps.winfo_children():
            w.destroy()
        for i, (title, (state, detail), actions) in enumerate(steps):
            mark, colour = MARKS[state]
            tk.Label(self.steps, text=mark, fg=colour, font=("Segoe UI Symbol", 13)).grid(
                row=2 * i, column=0, rowspan=2, sticky="n", padx=(0, 8), pady=(6, 0))
            ttk.Label(self.steps, text=f"{i + 1}. {title}", font=("Segoe UI", 10, "bold")).grid(
                row=2 * i, column=1, sticky="w", pady=(6, 0))
            body = ttk.Frame(self.steps)
            body.grid(row=2 * i + 1, column=1, sticky="ew")
            ttk.Label(body, text=detail, foreground="#57606a", wraplength=560, justify="left").pack(anchor="w")
            if actions:
                row = ttk.Frame(body)
                row.pack(anchor="w", pady=(3, 0))
                for label, fn in actions:
                    b = ttk.Button(row, text=label, command=fn)
                    b.pack(side="left", padx=(0, 6))
                    if self.busy:
                        b.state(["disabled"])
        self.activity.configure(text="")

    # Actions that take a while run on a worker thread too.

    def say(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text.rstrip() + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def job(self, label, work):
        if self.busy:
            return
        self.busy = True
        self.activity.configure(text=label + "...")
        self.say(label + "...")

        def run():
            try:
                work()
                self.events.put(("done", label + ": done."))
            except Exception as e:
                self.events.put(("done", f"{label}: failed: {e}"))
        threading.Thread(target=run, daemon=True).start()

    def run_job(self, label, cmd):
        def work():
            p = subprocess.Popen(cmd, cwd=HERE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                 encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
            last = ""
            for line in p.stdout:
                # ollama pull redraws a progress bar; only log lines that change meaningfully.
                line = line.strip().split("\r")[-1]
                if line and line[:20] != last[:20]:
                    self.events.put(("log", line))
                last = line
            if p.wait():
                raise RuntimeError(f"exit code {p.returncode}")
        self.job(label, work)

    def send(self, files, folder):
        if self.launcher.proc:
            messagebox.showinfo("Setup guide", "Stop the bridge first (main window), then send.", parent=self)
            return
        pids = ti_software_pids()
        if pids:
            if not messagebox.askyesno("Setup guide", "TI-Nspire software is open and holds the USB link.\n\n"
                                       "Close it now?", parent=self):
                return
            powershell("Stop-Process -Force -Id " + ",".join(map(str, pids)))
        self.run_job(f"Sending to {folder} on the calculator (can take 30 s)",
                     [PYTHON, "-u", BRIDGE, "--send", *files, "--to", folder])

    def download_ndless(self):
        def work():
            url = github_asset(f"ndless-nspire/Ndless/releases/tags/{NDLESS_TAG}", lambda n: n.endswith(".zip"))
            os.makedirs(NDLESS_DIR, exist_ok=True)
            zpath = os.path.join(NDLESS_DIR, os.path.basename(url))
            download(url, zpath)
            with zipfile.ZipFile(zpath) as z:
                z.extractall(NDLESS_DIR)
        self.job(f"Downloading Ndless {NDLESS_TAG} from GitHub", work)

    def download_llm(self):
        def work():
            url = github_asset(f"{LLM_REPO}/releases/latest", lambda n: n == "llm.tns")
            download(url, LLM_TNS)
        self.job("Downloading llm.tns from GitHub", work)

    def enter_key(self, name, what):
        value = simpledialog.askstring("Setup guide", f"Paste your {what}:", show="*", parent=self)
        if value and value.strip():
            save_api_key(name, value.strip())
            self.say(f"Saved your {what} for this Windows account.")
            self.refresh()

    def poll(self):
        while not self.events.empty():
            kind, value = self.events.get()
            if kind == "steps":
                self.show_steps(value)
            elif kind == "log":
                self.say(value)
            elif kind == "done":
                self.busy = False
                self.say(value)
                self.refresh()
        self.after(200, self.poll)
