"""Relay prompts from a TI-Nspire (llm.tns over USB) to a local Ollama model.

Usage:
  python bridge.py                 # serve the calculator
  python bridge.py --echo          # spike: echo back whatever the calc sends
  python bridge.py --selftest "What is 2+2?"   # Ollama only, no calculator
  python bridge.py --cloud         # use ollama.com (needs OLLAMA_API_KEY)
  python bridge.py --send ../calc/llm.tns --to /llm   # copy files to the calculator
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

import navnet

SERVICE_ID = 0x5001
LOCAL_MODEL = "llama3.2:3b"
CLOUD_URL = "https://ollama.com"
CLOUD_MODEL = "gpt-oss:120b"
SYSTEM_PROMPT = (
    "You are running on a TI-Nspire calculator screen (about 50 chars wide, 25 lines). "
    "Answer concisely in plain ASCII text. No markdown, no LaTeX, no tables, no emoji. "
    "Write math inline like x^2, sqrt(x), d/dx, pi, <=. Prefer short, direct answers."
)

ASCII_MAP = {
    "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u2013": "-", "\u2014": "-",
    "\u2026": "...", "\u00d7": "*", "\u00f7": "/", "\u2212": "-", "\u2264": "<=", "\u2265": ">=",
    "\u2260": "!=", "\u2248": "~=", "\u221a": "sqrt", "\u03c0": "pi", "\u221e": "inf",
    "\u00b2": "^2", "\u00b3": "^3", "\u00b0": " deg", "\u2192": "->", "\u21d2": "=>",
    "\u03b8": "theta", "\u0394": "delta", "\u03b1": "alpha", "\u03b2": "beta", "\u00b1": "+/-",
    "\u222b": "integral", "\u2211": "sum", "\u00b7": "*", "\u2022": "-", "\u00a0": " ",
}


def to_calc_text(text, max_chars):
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)  # reasoning models
    text = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: m.group(1) or m.group(2), text)
    text = re.sub(r"`{1,3}([^`]*)`{1,3}", r"\1", text)
    text = re.sub(r"^#+\s*", "", text, flags=re.M)
    text = re.sub(r"\\[()\[\]]", "", text)
    text = "".join(ASCII_MAP.get(c, c) for c in text)
    text = text.encode("ascii", "replace").decode().strip()
    if len(text) > max_chars:
        text = text[: max_chars - 3] + "..."
    return text


class Ollama:
    def __init__(self, url, model, history_turns, api_key=None):
        self.url = url.rstrip("/") + "/api/chat"
        self.model = model
        self.history_turns = history_turns
        self.history = []
        self.headers = {"Content-Type": "application/json"}
        if api_key:
            self.headers["Authorization"] = "Bearer " + api_key

    def ask(self, prompt):
        if prompt.strip().lower() in ("/new", "/reset", "clear"):
            self.history = []
            return "Conversation cleared."
        msgs = self.history[-2 * self.history_turns :] + [{"role": "user", "content": prompt}]
        body = json.dumps({"model": self.model, "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + msgs,
                           "stream": False}).encode()
        req = urllib.request.Request(self.url, body, self.headers)
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                reply = json.load(r)["message"]["content"]
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace").strip()[:200]
            if e.code == 401:
                raise RuntimeError("API key rejected (401). Check OLLAMA_API_KEY.") from None
            raise RuntimeError(f"HTTP {e.code}: {detail}") from None
        # Only keep a turn in the history once it succeeded.
        self.history += [{"role": "user", "content": prompt}, {"role": "assistant", "content": reply}]
        return reply


def make_llm(args):
    if args.cloud:
        key = os.environ.get("OLLAMA_API_KEY")
        if not key:
            sys.exit("--cloud needs an API key: create one at https://ollama.com/settings/keys "
                     "and set the OLLAMA_API_KEY environment variable.")
        return Ollama(CLOUD_URL, args.model or CLOUD_MODEL, args.history, key)
    return Ollama(args.ollama, args.model or LOCAL_MODEL, args.history)


def quiet_navnet(verbose):
    """navnet.dll logs heavily to the C-level stdout/stderr; send it to a file.

    Returns a file object that still writes to the real console."""
    if verbose:
        return sys.stdout
    log = os.open(os.path.join(os.path.dirname(__file__), "navnet.log"), os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
    sys.stdout.flush()
    console = os.fdopen(os.dup(1), "w", buffering=1)
    os.dup2(log, 1)
    os.dup2(log, 2)
    return console


def send_files(args):
    nn = navnet.NavNet()
    console = quiet_navnet(args.verbose)
    nn.init(debug=args.debug)
    try:
        print("waiting for calculator (can take up to 30s)...", file=console, flush=True)
        for f in args.send:
            remote = args.to.rstrip("/") + "/" + os.path.basename(f)
            print(f"sending {f} -> {remote}", file=console, flush=True)
            nn.put_file(f, remote)
        print("done", file=console)
    except navnet.NavNetError as e:
        print(f"error: {e}", file=console)
        sys.exit(1)
    finally:
        nn.shutdown()


def serve(args):
    nn = navnet.NavNet()
    console = quiet_navnet(args.verbose)

    def say(*a):
        print(time.strftime("%H:%M:%S"), *a, file=console, flush=True)

    llm = None if args.echo else make_llm(args)

    def handle(ch):
        say("calculator connected")
        try:
            # Handshake (see calc/main.c HELLO): the calc speaks first, we echo it.
            hello = nn.read(ch, 10000)
            say("hello:", hello)
            nn.write(ch, b"LLM1")
            while True:
                prompt = navnet.recv_msg(nn, ch, timeout_ms=0x7FFFFFFF).decode("ascii", "replace")
                say("Q:", prompt)
                if llm is None:
                    reply = "echo: " + prompt
                else:
                    try:
                        reply = to_calc_text(llm.ask(prompt), args.max_chars)
                    except Exception as e:
                        reply = f"[bridge error] {e}"
                say("A:", reply)
                navnet.send_msg(nn, ch, reply.encode("ascii", "replace"))
        except navnet.NavNetError as e:
            say("connection closed:", e)
        # No TI_NN_Disconnect here: on the serving side it tears down the whole
        # service, not just this connection, so llm.tns could not reconnect.
        # The calculator closes its end; navnet cleans up when we return.

    nn.init(debug=args.debug)
    # navnet runs each connection on its own thread and deletes the connection
    # as soon as the callback returns, so the whole session happens inside it.
    nn.start_service(args.service_id, handle)
    mode = "echo" if args.echo else f"model {llm.model}" + (" on ollama.com" if args.cloud else "")
    say(f"service 0x{args.service_id:04x} started ({mode}); waiting for the calculator's USB link...")
    try:
        # navnet only brings up the USB link to the calculator while someone
        # enumerates nodes, so keep polling; otherwise llm.tns has no route here.
        present = False
        while True:
            now = bool(nn.nodes(wait_s=0))
            if now != present:
                present = now
                say("calculator link up - run llm.tns now. Ctrl+C to quit." if now else "calculator link lost")
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        nn.stop_service(args.service_id)
        nn.shutdown()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", help=f"default: {LOCAL_MODEL} locally, {CLOUD_MODEL} with --cloud")
    ap.add_argument("--ollama", default="http://localhost:11434", help="local Ollama URL")
    ap.add_argument("--cloud", action="store_true",
                    help=f"use {CLOUD_URL} instead of local Ollama (API key from OLLAMA_API_KEY)")
    ap.add_argument("--service-id", type=lambda s: int(s, 0), default=SERVICE_ID)
    ap.add_argument("--max-chars", type=int, default=3000)
    ap.add_argument("--history", type=int, default=6, help="conversation turns to remember")
    ap.add_argument("--echo", action="store_true", help="echo mode for testing the USB link")
    ap.add_argument("--selftest", metavar="PROMPT", help="ask Ollama once and print the calc-formatted reply")
    ap.add_argument("--send", nargs="+", metavar="FILE", help="copy .tns files to the calculator, then exit")
    ap.add_argument("--to", default="/", metavar="FOLDER", help="calculator folder for --send (default: My Documents root)")
    ap.add_argument("--verbose", action="store_true", help="show navnet.dll log in the console")
    ap.add_argument("--debug", type=int, default=0, help="navnet.dll debug level (0-4)")
    args = ap.parse_args()

    if args.selftest:
        try:
            print(to_calc_text(make_llm(args).ask(args.selftest), args.max_chars))
        except (RuntimeError, OSError) as e:
            sys.exit(f"error: {e}")
        return
    if args.send:
        send_files(args)
        return
    serve(args)


if __name__ == "__main__":
    main()
