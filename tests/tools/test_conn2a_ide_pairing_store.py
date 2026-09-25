import base64
import json
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

from src.tools.ide_pairing import PairingError
from src.tools.ide_pairing_store import PairingStore

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows DPAPI and ACL contract")


def test_secret_is_os_encrypted_and_can_be_read_by_second_instance(tmp_path):
    store = PairingStore(tmp_path / "pairing")
    key = store.initialize()
    raw = store.path.read_bytes()
    assert key.secret not in raw
    assert base64.b64encode(key.secret) not in raw
    assert PairingStore(store.directory).load() == key
    assert store.initialize() == key


def test_revoke_is_persistent_and_initialize_cannot_undo_it(tmp_path):
    store = PairingStore(tmp_path / "pairing")
    first = store.initialize()
    store.revoke()
    assert store.load() is None
    assert PairingStore(store.directory).initialize() is None
    assert "encrypted" not in json.loads(store.path.read_text())
    second = store.rotate()
    assert second.key_id != first.key_id and second.secret != first.secret
    assert store.load() == second


@pytest.mark.parametrize("raw", [b"broken", b"{}", b"x" * 5000])
def test_corruption_fails_closed_without_replacing_the_file(tmp_path, raw):
    store = PairingStore(tmp_path / "pairing")
    store.initialize()
    store.path.write_bytes(raw)
    with pytest.raises(PairingError):
        store.initialize()
    assert store.path.read_bytes() == raw


def test_ciphertext_tampering_is_not_accepted(tmp_path):
    store = PairingStore(tmp_path / "pairing")
    store.initialize()
    value = json.loads(store.path.read_text())
    value["encrypted"] = base64.b64encode(b"not a DPAPI ciphertext").decode()
    store.path.write_text(json.dumps(value))
    with pytest.raises(PairingError):
        store.load()


def test_concurrent_initializers_share_exactly_one_key(tmp_path):
    directory = tmp_path / "pairing"
    with ThreadPoolExecutor(max_workers=4) as pool:
        keys = list(pool.map(lambda _: PairingStore(directory).initialize(), range(4)))
    assert len({key.key_id for key in keys}) == 1
    assert len({key.secret for key in keys}) == 1


def test_directory_acl_has_no_inherited_or_general_user_grant(tmp_path):
    import win32api
    import win32security

    store = PairingStore(tmp_path / "pairing")
    store.initialize()
    descriptor = win32security.GetNamedSecurityInfo(str(store.directory), win32security.SE_FILE_OBJECT,
                                                   win32security.DACL_SECURITY_INFORMATION)
    dacl = descriptor.GetSecurityDescriptorDacl()
    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32security.TOKEN_QUERY)
    try:
        user = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
    finally:
        token.Close()
    allowed = {str(user), str(win32security.CreateWellKnownSid(win32security.WinLocalSystemSid, None))}
    assert descriptor.GetSecurityDescriptorControl()[0] & win32security.SE_DACL_PROTECTED
    direct = set()
    inheritable = set()
    for index in range(dacl.GetAceCount()):
        header, mask, sid = dacl.GetAce(index)
        assert header[0] == win32security.ACCESS_ALLOWED_ACE_TYPE
        assert not header[1] & win32security.INHERITED_ACE
        assert str(sid) in allowed
        assert mask in {0x10000000, 0x1F01FF}
        if not header[1] & win32security.INHERIT_ONLY_ACE:
            direct.add(str(sid))
        if header[1] & win32security.OBJECT_INHERIT_ACE:
            inheritable.add(str(sid))
    assert direct == inheritable == allowed
