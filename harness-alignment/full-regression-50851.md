# 构造边界修复后非CLI全量（已退出1）

50851实际退出1（b05d1f）；799 failed、14801 passed、1 skipped、103 warnings，
613.79s。唯一skip为文件系统拒绝非UTF-8文件名。完整日志保留在
full-regression-50851.txt，JUnit仍在命令指定目录。大量失败显示协程被当作
Runtime使用，与跨文件helper迁移遗漏一致；须逐分类核对，不能先声称全部同因。

句柄50851，8 workers/loadfile/禁止重启。资源与迁移范围见
sync-in-loop-construction-decision.md。日志与JUnit直接落盘；等待实际退出码。

```sh
PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring CORKI_TEST_SANDBOX_COMPILER=/Users/corki/PycharmProjects/corki/src/corki/_native/sandbox/corki-sandbox CORKI_TEST_PRE_METADATA_COMPILER=/private/tmp/corki-metadata.XnlekR/source-bundle-before-211/corki-sandbox CORKI_TEST_PRE_NATIVE_FS_COMPILER=/private/tmp/corki-native-fs.w6KpJ7/source-bundle-before-213/corki-sandbox CORKI_TEST_PRE_NATIVE_PATCH_COMPILER=/private/tmp/corki-native-patch.jwlual/source-bundle-before-214/corki-sandbox CORKI_TEST_PRE_PATCH_APPROVAL_COMPILER=/private/tmp/corki-patch-approval.FRW1tt/source-bundle-before-215/corki-sandbox CORKI_TEST_PRE_PATCH_DELTA_COMPILER=/private/tmp/corki-patch-delta.e7I0yC/source-bundle-before-216/corki-sandbox .venv/bin/pytest tests/unit tests/integration --ignore=tests/unit/cli --ignore-glob='tests/integration/test_cli*.py' -n 8 --dist=loadfile --max-worker-restart=0 -o addopts='' -q --tb=short -rs --junitxml=/private/tmp/corki-construction-regression.BHOX16/results.xml > /private/tmp/corki-construction-regression.BHOX16/pytest.log 2>&1
```
