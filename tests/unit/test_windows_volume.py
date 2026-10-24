import pytest

from corki.platforms.windows_volume import is_local_volume_root


@pytest.mark.parametrize("name", [
    "\\Device\\HarddiskVolume0\\",
    "\\Device\\HarddiskVolume123\\",
    "\\device\\harddiskvolume4\\",
])
def test_only_physical_disk_volume_roots_are_accepted(name):
    assert is_local_volume_root(name)


@pytest.mark.parametrize("name", [
    "\\Device\\NamedPipe\\",
    "\\Device\\HarddiskVolume\\",
    "\\Device\\HarddiskVolume1\\directory\\",
    "\\Device\\HarddiskVolume1suffix\\",
    "\\Device\\HarddiskVolumeShadowCopy1\\",
    "\\Device\\CdRom0\\",
    "\\??\\C:\\",
    "\\Device\\HarddiskVolume١\\",
])
def test_volume_retry_rejects_devices_subpaths_and_aliases(name):
    assert not is_local_volume_root(name)
