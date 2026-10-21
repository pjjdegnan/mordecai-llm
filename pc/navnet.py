"""ctypes bindings for TI's navnet.dll (ships with TI-Nspire Student/Teacher Software).

Signatures follow ndless-sdk/NavNet/include/navnet.h. Init options follow TI's own navnet.jar
test harness: "-c 4 -d <level> -p <connectors dir>". Looks for a private copy
in pc/ti/ first, then the TI desktop install.
"""
import ctypes
import os
import time
from ctypes import c_char_p, c_int16, c_uint32, c_void_p, POINTER

HERE = os.path.dirname(os.path.abspath(__file__))
TI_DIRS = [
    os.path.join(HERE, "ti"),  # private copy, survives uninstalling the TI desktop app
    r"C:\Program Files\TI Education\TI-Nspire CX CAS Student Software\lib",
    r"C:\Program Files\TI Education\TI-Nspire CX Student Software\lib",
    r"C:\Program Files\TI Education\TI-Nspire CX CAS Premium Teacher Software\lib",
    r"C:\Program Files\TI Education\TI-Nspire CX Premium Teacher Software\lib",
]

SERVICE_CB = ctypes.CFUNCTYPE(None, c_void_p, c_void_p)

PKT_MAX = 254  # NavNet packet payload limit on a USB-connected calculator
FILE_SERVICE = 0x4060  # TI_NN_SERVICE_SYNC: file transfer on the calculator


class NavNetError(RuntimeError):
    pass


def _find_lib_dir():
    override = os.environ.get("NAVNET_DIR")
    for d in ([override] if override else []) + TI_DIRS:
        if d and os.path.isfile(os.path.join(d, "navnet.dll")):
            return d
    raise NavNetError("navnet.dll not found; install TI-Nspire Student Software or set NAVNET_DIR")


def _check(rc, what):
    # navnet.dll returns 0 or 1 on success; negative values are errors.
    if rc < 0:
        raise NavNetError(f"{what} failed: {rc}")


class NavNet:
    def __init__(self, lib_dir=None):
        self.lib_dir = lib_dir or _find_lib_dir()
        conn_dir = os.path.join(self.lib_dir, "connectors")
        os.add_dll_directory(self.lib_dir)
        # Connectors are loaded by navnet.dll itself, which only searches PATH
        # for their dependencies (e.g. bridgelink.dll).
        os.environ["PATH"] = conn_dir + os.pathsep + self.lib_dir + os.pathsep + os.environ.get("PATH", "")
        self.dll = ctypes.CDLL(os.path.join(self.lib_dir, "navnet.dll"))
        d = self.dll
        d.TI_NN_Init.argtypes = [c_char_p]
        d.TI_NN_Init.restype = c_int16
        d.TI_NN_Shutdown.argtypes = []
        d.TI_NN_Shutdown.restype = c_int16
        d.TI_NN_StartService.argtypes = [c_uint32, c_void_p, SERVICE_CB]
        d.TI_NN_StartService.restype = c_int16
        d.TI_NN_StopService.argtypes = [c_uint32]
        d.TI_NN_StopService.restype = c_int16
        d.TI_NN_Read.argtypes = [c_void_p, c_uint32, c_void_p, c_uint32, POINTER(c_uint32)]
        d.TI_NN_Read.restype = c_int16
        d.TI_NN_Write.argtypes = [c_void_p, c_void_p, c_uint32]
        d.TI_NN_Write.restype = c_int16
        d.TI_NN_Disconnect.argtypes = [c_void_p]
        d.TI_NN_Disconnect.restype = c_int16
        d.TI_NN_GetConnMaxPktSize.argtypes = [c_void_p]
        d.TI_NN_GetConnMaxPktSize.restype = c_uint32
        d.TI_NN_CreateOperationHandle.argtypes = []
        d.TI_NN_CreateOperationHandle.restype = c_void_p
        d.TI_NN_DestroyOperationHandle.argtypes = [c_void_p]
        d.TI_NN_DestroyOperationHandle.restype = c_int16
        d.TI_NN_NodeEnumInit.argtypes = [c_void_p]
        d.TI_NN_NodeEnumInit.restype = c_int16
        d.TI_NN_NodeEnumNext.argtypes = [c_void_p, POINTER(c_void_p)]
        d.TI_NN_NodeEnumNext.restype = c_int16
        d.TI_NN_NodeEnumDone.argtypes = [c_void_p]
        d.TI_NN_NodeEnumDone.restype = c_int16
        d.TI_NN_Connect.argtypes = [c_void_p, c_uint32, POINTER(c_void_p)]
        d.TI_NN_Connect.restype = c_int16
        d.TI_NN_PutFile.argtypes = [c_void_p, c_void_p, c_char_p, c_char_p]
        d.TI_NN_PutFile.restype = c_int16
        d.TI_NN_MkDir.argtypes = [c_void_p, c_void_p, c_char_p]
        d.TI_NN_MkDir.restype = c_int16
        self._callbacks = {}  # keep CFUNCTYPE objects alive

    def init(self, opts=None, debug=0):
        if opts is None:
            opts = f"-c 4 -d {debug} -p " + os.path.join(self.lib_dir, "connectors")
        _check(self.dll.TI_NN_Init(opts.encode()), f"TI_NN_Init({opts!r})")

    def shutdown(self):
        self.dll.TI_NN_Shutdown()

    def start_service(self, service_id, handler):
        """handler(ch) is called (on a navnet thread) for each incoming connection."""
        cb = SERVICE_CB(lambda ch, data: handler(ch))
        _check(self.dll.TI_NN_StartService(service_id, None, cb), f"TI_NN_StartService(0x{service_id:04x})")
        self._callbacks[service_id] = cb

    def stop_service(self, service_id):
        self.dll.TI_NN_StopService(service_id)
        self._callbacks.pop(service_id, None)

    def max_pkt(self, ch):
        # The DLL reports 1460 for USB, but the CX II rejects packets over 254
        # bytes (same reason put_file uses small chunks).
        return min(self.dll.TI_NN_GetConnMaxPktSize(ch) or PKT_MAX, PKT_MAX)

    def read(self, ch, timeout_ms=60000, size=4096):
        buf = ctypes.create_string_buffer(size)
        got = c_uint32(0)
        _check(self.dll.TI_NN_Read(ch, timeout_ms, buf, size, ctypes.byref(got)), "TI_NN_Read")
        return buf.raw[: got.value]

    def write(self, ch, data):
        _check(self.dll.TI_NN_Write(ch, data, len(data)), "TI_NN_Write")

    def disconnect(self, ch):
        self.dll.TI_NN_Disconnect(ch)

    def nodes(self, wait_s=30):
        """Return handles of connected calculators, waiting briefly for USB enumeration."""
        deadline = time.time() + wait_s
        while True:
            found = []
            oh = self.dll.TI_NN_CreateOperationHandle()
            if self.dll.TI_NN_NodeEnumInit(oh) >= 0:
                for _ in range(16):
                    nh = c_void_p()
                    # Next can keep returning the last node instead of failing.
                    if self.dll.TI_NN_NodeEnumNext(oh, ctypes.byref(nh)) < 0 or not nh.value or nh.value in found:
                        break
                    found.append(nh.value)
                self.dll.TI_NN_NodeEnumDone(oh)
            self.dll.TI_NN_DestroyOperationHandle(oh)
            if found or time.time() > deadline:
                return found
            time.sleep(0.5)

    def connect(self, nh, service_id):
        ch = c_void_p()
        _check(self.dll.TI_NN_Connect(nh, service_id, ctypes.byref(ch)), f"TI_NN_Connect(0x{service_id:04x})")
        return ch.value

    def put_file(self, local_path, remote_path):
        """Copy a local file to the first connected calculator, creating parent
        folders and replacing any existing file."""
        nodes = self.nodes()
        if not nodes:
            raise NavNetError("no calculator found (plugged in? TI software closed?)")
        ch = self.connect(nodes[0], FILE_SERVICE)
        oh = self.dll.TI_NN_CreateOperationHandle()
        try:
            parts = remote_path.strip("/").split("/")[:-1]
            for i in range(len(parts)):
                # MkDir fails harmlessly when the folder already exists.
                self.dll.TI_NN_MkDir(ch, oh, ("/" + "/".join(parts[: i + 1])).encode())
        finally:
            self.dll.TI_NN_DestroyOperationHandle(oh)
            self.disconnect(ch)
        # TI_NN_PutFile sends 1460-byte chunks, which a USB-connected CX II
        # rejects after the first one, so speak the file protocol directly
        # (as libnspire's nspire_file_write does): 253-byte data chunks.
        with open(local_path, "rb") as f:
            data = f.read()
        ch = self.connect(nodes[0], FILE_SERVICE)
        try:
            self.write(ch, b"\x03\x01" + remote_path.encode() + b"\x00" + len(data).to_bytes(4, "big"))
            reply = self.read(ch, 10000)
            if reply[:1] != b"\x04":
                raise NavNetError(f"calculator refused {remote_path} (reply {reply.hex()})")
            for i in range(0, len(data), 253):
                self.write(ch, b"\x05" + data[i : i + 253])
            reply = self.read(ch, 30000)
            if reply[:2] != b"\xff\x00":
                raise NavNetError(f"transfer of {remote_path} failed (reply {reply.hex()})")
        finally:
            self.disconnect(ch)


# Framing shared with calc/main.c: 4-byte little-endian length, then payload,
# split into packets no larger than the connection's max packet size.

def send_msg(nn, ch, payload):
    data = len(payload).to_bytes(4, "little") + payload
    step = nn.max_pkt(ch)
    for i in range(0, len(data), step):
        nn.write(ch, data[i : i + step])


def recv_msg(nn, ch, timeout_ms=60000):
    buf = b""
    while len(buf) < 4:
        buf += nn.read(ch, timeout_ms)
    n = int.from_bytes(buf[:4], "little")
    while len(buf) < 4 + n:
        buf += nn.read(ch, timeout_ms)
    return buf[4 : 4 + n]
