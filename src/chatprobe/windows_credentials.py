"""Credenciais locais do usuário no Windows."""

import ctypes
from ctypes import wintypes


CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2
ERROR_NOT_FOUND = 1168
MAX_BLOB_BYTES = 2560
DEFAULT_KEY_NAME = "Padrão"


class Credential(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(wintypes.BYTE)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


CredentialPointer = ctypes.POINTER(Credential)
CredentialArray = ctypes.POINTER(CredentialPointer)


def credential_target(provider, name):
    if not name.strip() or not name.isprintable() or any(char in name for char in "\\/*"):
        raise ValueError("Informe um nome para a chave sem barras ou asteriscos.")
    target = f"ChatProbe/API/{provider}"
    if name.casefold() == DEFAULT_KEY_NAME.casefold():
        return target
    return f"{target}/{name}"


class WindowsCredentials:
    def __init__(self):
        api = ctypes.WinDLL("advapi32", use_last_error=True)
        self.write = api.CredWriteW
        self.write.argtypes = [ctypes.POINTER(Credential), wintypes.DWORD]
        self.write.restype = wintypes.BOOL
        self.read = api.CredReadW
        self.read.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(CredentialPointer),
        ]
        self.read.restype = wintypes.BOOL
        self.enumerate = api.CredEnumerateW
        self.enumerate.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(CredentialArray),
        ]
        self.enumerate.restype = wintypes.BOOL
        self.delete = api.CredDeleteW
        self.delete.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        self.delete.restype = wintypes.BOOL
        self.free = api.CredFree
        self.free.argtypes = [ctypes.c_void_p]
        self.free.restype = None

    def list_names(self, provider):
        prefix = f"ChatProbe/API/{provider}/"
        count = wintypes.DWORD()
        credentials = CredentialArray()
        names = []
        if self.enumerate(f"{prefix}*", 0, ctypes.byref(count), ctypes.byref(credentials)):
            try:
                for index in range(count.value):
                    credential = credentials[index].contents
                    if credential.Type == CRED_TYPE_GENERIC:
                        names.append(credential.TargetName[len(prefix):])
            finally:
                self.free(credentials)
        else:
            error = ctypes.get_last_error()
            if error != ERROR_NOT_FOUND:
                raise ctypes.WinError(error)
        names.sort(key=str.casefold)
        if self.exists(provider):
            names.insert(0, DEFAULT_KEY_NAME)
        return names

    def save(self, provider, key, name=DEFAULT_KEY_NAME):
        target = credential_target(provider, name)
        encoded = key.encode("utf-8")
        if not encoded or len(encoded) > MAX_BLOB_BYTES:
            raise ValueError("Informe uma chave de API válida para salvar.")
        buffer = (wintypes.BYTE * len(encoded)).from_buffer_copy(encoded)
        credential = Credential(
            Type=CRED_TYPE_GENERIC, TargetName=target,
            CredentialBlobSize=len(encoded), CredentialBlob=buffer,
            Persist=CRED_PERSIST_LOCAL_MACHINE, UserName=provider,
        )
        try:
            if not self.write(ctypes.byref(credential), 0):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            ctypes.memset(buffer, 0, len(encoded))

    def _read_credential(self, provider, name):
        credential = CredentialPointer()
        if self.read(credential_target(provider, name), CRED_TYPE_GENERIC, 0, ctypes.byref(credential)):
            return credential
        error = ctypes.get_last_error()
        if error != ERROR_NOT_FOUND:
            raise ctypes.WinError(error)
        return None

    def exists(self, provider, name=DEFAULT_KEY_NAME):
        credential = self._read_credential(provider, name)
        if credential is None:
            return False
        self.free(credential)
        return True

    def get(self, provider, name=DEFAULT_KEY_NAME):
        credential = self._read_credential(provider, name)
        if credential is None:
            return None
        try:
            blob = credential.contents
            return ctypes.string_at(blob.CredentialBlob, blob.CredentialBlobSize).decode("utf-8")
        finally:
            self.free(credential)

    def remove(self, provider, name=DEFAULT_KEY_NAME):
        if not self.delete(credential_target(provider, name), CRED_TYPE_GENERIC, 0):
            error = ctypes.get_last_error()
            if error != ERROR_NOT_FOUND:
                raise ctypes.WinError(error)
