"""Windows-user-bound secret storage using DPAPI."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from pathlib import Path


SECRET_DIR = Path(r"D:\environment\shuzhiren\secrets")


class DataBlob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD),
                ("data", ctypes.POINTER(ctypes.c_ubyte))]


def _blob(data: bytes):
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    return DataBlob(len(data), buffer), buffer


def protect(value: str, path: str | Path):
    raw = value.encode("utf-8")
    source, source_buffer = _blob(raw)
    result = DataBlob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    crypt.CryptProtectData.argtypes = [
        ctypes.POINTER(DataBlob), ctypes.c_wchar_p, ctypes.POINTER(DataBlob),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(DataBlob)]
    crypt.CryptProtectData.restype = wintypes.BOOL
    if not crypt.CryptProtectData(ctypes.byref(source), None, None, None,
                                  None, 0, ctypes.byref(result)):
        raise ctypes.WinError(ctypes.get_last_error())
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    try:
        encrypted = ctypes.string_at(result.data, result.size)
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(encrypted)
    finally:
        ctypes.memset(source_buffer, 0, len(raw))
        kernel.LocalFree(result.data)


def unprotect(path: str | Path):
    encrypted = Path(path).read_bytes()
    source, source_buffer = _blob(encrypted)
    result = DataBlob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    crypt.CryptUnprotectData.argtypes = [
        ctypes.POINTER(DataBlob), ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(DataBlob)]
    crypt.CryptUnprotectData.restype = wintypes.BOOL
    if not crypt.CryptUnprotectData(ctypes.byref(source), None, None, None,
                                    None, 0, ctypes.byref(result)):
        raise ctypes.WinError(ctypes.get_last_error())
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    try:
        return ctypes.string_at(result.data, result.size).decode("utf-8")
    finally:
        ctypes.memset(source_buffer, 0, len(encrypted))
        ctypes.memset(result.data, 0, result.size)
        kernel.LocalFree(result.data)
