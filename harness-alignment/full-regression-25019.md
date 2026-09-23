# 2026-09-14 非 CLI 回归（已退出 0）

实际句柄终态：25019 退出0，工具输出299418。日志d0fc09：
**15598 passed, 1 skipped in 553.20s (0:09:13)**。
唯一跳过：test_project_trust_selection.py:164，文件系统拒绝非UTF-8文件名。
本批包含stdin观察修订及扩展world-state接口；没有失败或worker重启。
不将此次全量通过等同于尚未实现/尚待范围确认的A–E能力全部完成。

执行句柄：25019。启动基线 Corki e53a368，工作树原本干净。
51160 已无句柄且无测试进程，最终输出丢失，不能认定通过；本批重新取证。
12 逻辑核、18 GiB、系统空闲38%，8 workers，loadfile，禁止worker重启。
沿用已审查的独立临时数据库/home/端口、进程内catalog及只读compiler隔离。
生产和测试在运行期间保持不变。CLI排除不等于整体A–E验收完成。

```sh
PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring CORKI_TEST_SANDBOX_COMPILER=/Users/corki/PycharmProjects/corki/src/corki/_native/sandbox/corki-sandbox CORKI_TEST_PRE_METADATA_COMPILER=/private/tmp/corki-metadata.XnlekR/source-bundle-before-211/corki-sandbox CORKI_TEST_PRE_NATIVE_FS_COMPILER=/private/tmp/corki-native-fs.w6KpJ7/source-bundle-before-213/corki-sandbox CORKI_TEST_PRE_NATIVE_PATCH_COMPILER=/private/tmp/corki-native-patch.jwlual/source-bundle-before-214/corki-sandbox CORKI_TEST_PRE_PATCH_APPROVAL_COMPILER=/private/tmp/corki-patch-approval.FRW1tt/source-bundle-before-215/corki-sandbox CORKI_TEST_PRE_PATCH_DELTA_COMPILER=/private/tmp/corki-patch-delta.e7I0yC/source-bundle-before-216/corki-sandbox .venv/bin/pytest tests/unit tests/integration --ignore=tests/unit/cli --ignore-glob='tests/integration/test_cli*.py' -n 8 --dist=loadfile --max-worker-restart=0 -o addopts='' -q --tb=short -rs --junitxml=/private/tmp/corki-harness-regression.XUK755/results.xml > /private/tmp/corki-harness-regression.XUK755/pytest.log 2>&1
```

日志直接保存在命令指定目录，不依赖对话输出累积。JUnit仅是测试结果证据，
执行句柄的实际退出码也已取得，故本批可记录通过。
