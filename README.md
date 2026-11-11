# mordecai-llm

Type a question on a TI-Nspire CX II and get an answer from an LLM on your Windows PC. It uses only the normal USB link cable, with no extra hardware. The LLM runs locally through Ollama, on ollama.com, or as Claude through the Anthropic API.

```
calculator: llm.tns (Ndless C)  --USB link (NavNet service 0x5001)-->  pc/bridge.py (TI navnet.dll)  --HTTP-->  Ollama
```

- `calc/`: the calculator program (`main.c`), built with the Ndless SDK
- `pc/navnet.py`: Python bindings for TI's `navnet.dll`, from TI-Nspire Student Software
- `pc/bridge.py`: hosts the link service, forwards prompts to Ollama, and can copy files to the calculator

## Requirements

- TI-Nspire CX II / CX II-T / CX II CAS on **OS 6.2.0.333 or 6.4.0.74**, the versions [Ndless](https://github.com/ndless-nspire/Ndless) supports. TI doesn't allow downgrading the OS.
- Windows with **TI-Nspire CX (CAS) Student or Teacher Software** installed. The bridge uses its `navnet.dll` and USB driver; the software itself doesn't need to run.
- Python 3, 64-bit
- One LLM backend:
  - [Ollama](https://ollama.com) with a model pulled (default `llama3.2:3b`)
  - an ollama.com API key, for `--cloud`
  - an Anthropic API key plus `python -m pip install anthropic`, for `--claude`

### Optional: keep TI's DLL working if the TI software goes away

`navnet.py` looks for a private copy of the link library in `pc/ti/` first. To make one, copy `navnet.dll` from the TI software's `lib` folder into `pc/ti/`, its `lib/connectors/*.dll` into `pc/ti/connectors/`, and `bridgelink.dll` from the install folder into `pc/ti/`. The folder is git-ignored because it's TI's code; don't redistribute it. TI's USB driver must stay installed.

## One-time setup

1. **Ndless on the calculator.** Download the [Ndless r2022 release](https://github.com/ndless-nspire/Ndless/releases) and unpack it into `ndless-release/`. Close the TI software, then send the files and open the installer document on the calculator:
   ```bash
   python pc/bridge.py --send ndless-release/ndless_resources.tns ndless-release/ndless_installer_4.5.5-6.2.0-6.4.0.tns --to /ndless
   ```
2. **`llm.tns` on the calculator.** Download it from this repo's [Releases](../../releases) into `calc/`, or build it (below), then:
   ```bash
   python pc/bridge.py --send calc/llm.tns --to /llm
   ```

## One-button launcher

`launcher.pyw` opens a small window with a **Start** button and a choice of local model, Ollama cloud, or Claude. Start closes TI-Nspire software if it's open, starts Ollama if needed (or loads your saved API key), runs the bridge, and tells you when to open `llm` on the calculator. Press esc on the calculator before pressing **Stop**.

**Setup guide:** the launcher's **Setup guide** button (opened automatically the first time) walks through every step in order. It ticks off what's already done and has buttons for what can be automated:
- downloading Ndless and `llm.tns`
- sending them to the calculator
- starting Ollama or downloading its model
- installing the Claude SDK
- saving API keys

Steps that happen on the calculator, such as checking the OS version and running the Ndless installer, are explained in place.

To get a desktop shortcut, point a new shortcut at `pythonw.exe` (next to your `python.exe`) with `launcher.pyw` as the argument.

The manual steps below do the same thing.

## Every time

1. **Close TI-Nspire Student Software.** It holds the USB link.
2. If the calculator has rebooted since you last ran Ndless, open `ndless/ndless_installer_4.5.5-6.2.0-6.4.0` on it. Ndless on OS 6.x does not survive a reboot. **Then stay on the home screen.**
3. Start the bridge and wait for `calculator link up`, usually 3-20 s:
   ```bash
   python pc/bridge.py
   ```
4. **Only now** open `llm/llm` on the calculator. It connects and shows `Connected.`
5. Type a question and press **enter**. Long answers pause with `-- more --`. Type `/new` to clear the chat history. Press **esc** to quit, or to cancel a pending answer.

The order matters. The bridge only sees the calculator while the calculator's OS is in control. If `llm` is already open when the bridge starts, the bridge never finds it, and `llm` reports "No bridge found".

## Using Ollama's cloud instead of a local model

Larger models such as `gpt-oss:120b` run on ollama.com instead of your GPU, and they're much better at math than `llama3.2:3b`. Your questions are sent to Ollama's servers, and usage counts against your Ollama account; see [ollama.com/pricing](https://ollama.com/pricing).

1. Create an API key at [ollama.com/settings/keys](https://ollama.com/settings/keys).
2. Store it as an environment variable (once), then open a new terminal so it takes effect:
   ```bash
   setx OLLAMA_API_KEY "paste-your-key-here"
   ```
   To use it in a terminal that was already open, load it with:
   ```bash
   $env:OLLAMA_API_KEY = [Environment]::GetEnvironmentVariable('OLLAMA_API_KEY','User')
   ```
3. Start the bridge in cloud mode:
   ```bash
   python pc/bridge.py --cloud
   ```
   Use another cloud model with `--model`, e.g. `--cloud --model kimi-k2.6`. List the current names with `curl https://ollama.com/api/tags`. Test without the calculator using `python pc/bridge.py --cloud --selftest "hi"`.

Never put the key in a file in this repo.

## Using Claude

`--claude` sends questions to Claude through the Anthropic API, using the official `anthropic` Python SDK. The default model is `claude-opus-5-5`. Usage is billed to your Anthropic account per token; see [pricing](https://platform.claude.com/docs/en/about-claude/pricing). A typical calculator question costs a fraction of a cent.

1. Install the SDK (once):
   ```bash
   python -m pip install anthropic
   ```
2. Create an API key at [platform.claude.com/settings/keys](https://platform.claude.com/settings/keys) and store it (once), then open a new terminal:
   ```bash
   setx ANTHROPIC_API_KEY "paste-your-key-here"
   ```
3. Test it without the calculator, then start the bridge:
   ```bash
   python pc/bridge.py --claude --selftest "what is the derivative of (2x+3)/(x+1)"
   ```
   ```bash
   python pc/bridge.py --claude
   ```

Options:
- **Other models:** `--claude --model claude-haiku-5-5` is much cheaper and faster for simple questions, and `claude-sonnet-5-5` sits in between.
- **Effort:** `--effort low|medium|high|xhigh|max` trades speed and cost for more careful reasoning (default `medium`). Try `high` for hard math.
- **Refusal fallback:** if Claude's safety filter wrongly declines a harmless question, the API retries it on Anthropic's recommended fallback model automatically (`fallbacks: "default"`), on the models that support it.

The launcher has a **Claude** option that does the same.

## Bridge options

| flag | meaning |
|---|---|
| `--model qwen3:4b` | model name (default `llama3.2:3b`, `gpt-oss:120b` with `--cloud`, `claude-opus-5-5` with `--claude`) |
| `--cloud` | use ollama.com instead of local Ollama; needs `OLLAMA_API_KEY` |
| `--claude` | use Claude via the Anthropic API; needs `ANTHROPIC_API_KEY` and the `anthropic` package |
| `--effort medium` | Claude only: `low`, `medium`, `high`, `xhigh` or `max` |
| `--echo` | link test: echoes the calculator's text back, no LLM |
| `--selftest "2+2?"` | LLM test only, no calculator |
| `--max-chars 3000` | cap on reply length |
| `--history 6` | turns of conversation remembered |
| `--service-id 0x5001` | must match `SERVICE_ID` in `calc/main.c` |
| `--send FILE... --to /folder` | copy files to the calculator, replacing existing ones (TI software not needed) |
| `--verbose` / `--debug 4` | show navnet.dll logs (otherwise written to `pc/navnet.log`) |

## Building llm.tns

The Ndless SDK builds on Linux; on Windows, use WSL (Ubuntu).

1. Build the SDK once (30–60 minutes), following [Ndless's instructions](https://github.com/ndless-nspire/Ndless/wiki/Ndless-SDK:-C-and-assembly-development-introduction). In short: clone `ndless-nspire/Ndless` with `--recursive`, run `ndless-sdk/toolchain/build_toolchain.sh`, then `make` in the repo root.
   - On Ubuntu 26.04, GDB fails to compile at the end of the toolchain build. It isn't needed: run `echo -n gdb-16.2 > .built_gdb` in `ndless-sdk/toolchain/` and rerun the script.
2. From this repo's `calc/` folder, with the SDK's `toolchain/install/bin` and `bin` on your `PATH`, run `make`.

## Troubleshooting

- **"No bridge found"** on the calculator: the bridge wasn't running with `calculator link up` before `llm` was opened. Quit `llm`, start the bridge, wait for `calculator link up`, then reopen `llm`.
- **Calculator freezes**: reset it (paperclip in the back hole, or hold doc + enter + EE), re-run the Ndless installer, then follow the steps above. Freezes have happened when the link failed or was closed mid-session. Don't stop the bridge while `llm` is connected; quit `llm` with esc first.
- **Connects but no reply**: try `--echo` to separate link problems from LLM problems, and check the LLM with `python pc/bridge.py --selftest hi`.
- **"no calculator found"**: the link takes 3–30 s to come up after the bridge starts, especially right after another run. Retry. In Git Bash, prefix commands with `MSYS_NO_PATHCONV=1`, otherwise `/ndless` gets rewritten to a Windows path. PowerShell and cmd are fine.
- **`llm` reopens itself after esc**: known quirk. The calculator's OS queues the keys you pressed while `llm` ran, including the **enter** used to send a question, and replays them after `llm` exits. That reopens the selected document. Press esc again; the relaunched copy reconnects normally.
- **Garbled symbols**: the bridge converts replies to ASCII. Add more mappings to `ASCII_MAP` in `pc/bridge.py`.

## How it works

The calculator side calls TI's NavNet link functions through the [Ndless](https://github.com/ndless-nspire/Ndless) SDK. The PC side loads TI's `navnet.dll` with `ctypes`, hosts a custom NavNet service, and polls node enumeration, which keeps the USB link up. Lessons from getting it working on a CX II (OS 6.2):

- `TI_NN_GetConnMaxPktSize` doesn't exist on CX II OS 6.x, and calling it freezes the calculator. Packets are fixed at 254 bytes.
- The PC only learns about a connection when the first data arrives, so the calculator sends a hello first.
- navnet.dll deletes a served connection when the service callback returns, so the whole session runs inside the callback. Calling `TI_NN_Disconnect` on the serving side removes the whole service.
- `TI_NN_PutFile` sends 1460-byte chunks, which a USB-connected CX II rejects, so `--send` speaks the file protocol directly in 253-byte chunks, as [libnspire](https://github.com/tangrs/libnspire) does.

## License

MIT for this project's code (see `LICENSE`). `llm.tns` links [nspire-io](https://github.com/Vogtinator/nspire-io), which is licensed under the LGPL 2.1; its source is at that link. TI-Nspire, NavNet and `navnet.dll` belong to Texas Instruments. This project is not affiliated with TI.
