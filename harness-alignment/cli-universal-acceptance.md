# CLI 通用对齐验收摘要

目标范围仅为 1、2、3、4、5、6、7、9；不包含官方服务或其他已排除功能。
逐项源码函数、测试演进和修复证据见 `cli-universal-progress.md`。
八项独立清单、端到端实现和最终相关回归已完成。下列差异和未验证环境仍明确保留。

## 功能与主要交互

| 项 | 使用方式 | 主要实现 |
| --- | --- | --- |
| 1 | 超过 1000 字符的粘贴折叠，提交展开；删除/撤销、排队取回保留真实内容 | composer_paste、paste_burst、inline_images、composer_validation |
| 2 | 输入引用前缀搜索文件、技能/插件，方向键选择、确认绑定、Esc 取消 | command_completion、reference_completion、native_file_search；runtime 输入引用链路 |
| 3 | 同次运行普通上下键恢复已提交草稿，图片、位置、长粘贴和引用一起恢复 | draft_history、inline_images、terminal |
| 4 | 粘贴单个有效图片路径自动转附件，保留 Ctrl+V 图片功能；失败保留文字 | image_clipboard、composer_paste |
| 5 | 读取/搜索聚合为 Exploring/Explored；长输出预览，Ctrl+T 查看完整历史 | tool_activity、terminal、history_view、history_rows、transcript |
| 6 | 本地 resume 列表：搜索、目录/归档筛选、预览、恢复、归档 | session_picker、session_catalog、resume_directory |
| 7 | 空输入 Esc 回溯，选择旧问题并确认，在新分支编辑；不重跑旧工具或回滚文件 | backtrack、backtrack_picker、backtrack_pages、application/runtime |
| 9 | 失焦时完成/需要操作通知，支持配置、OSC 9/BEL、安全降级 | notifications、terminal_responses、application |

会话列表使用上下键选择、Enter 恢复、Tab/左右键切换筛选、Ctrl+A 归档、
Ctrl+E 预览、Ctrl+T 查看历史；归档列表中 Enter 取消归档，数据不删除。
回溯界面 Esc/左键选更旧问题、右键选更新问题、Enter 编辑，q/Ctrl+T/Ctrl+C 取消。
运行中 Esc 保持中断语义，弹窗取消与回溯分开处理。

## Codex 源码依据

以下路径均相对于 `/Users/corki/IdeaProjects/ad/codex/codex-rs/`；
具体函数、测试与实际规则已逐项记录于 progress，而非仅按名称仿制。

- 1/3/4：`tui/src/bottom_pane/chat_composer.rs`、`paste_burst.rs`、
  `chat_composer_history.rs`，`tui/src/clipboard_paste.rs`，
  `tui/src/chatwidget/input_queue.rs`、`input_restore.rs`、`user_messages.rs`。
- 2：`tui/src/file_search.rs`、`file-search/src/lib.rs`、
  `tui/src/bottom_pane/mentions_v2/search_catalog.rs` 与 composer 候选交互。
- 5：`tui/src/exec_cell/model.rs`、`render.rs`，`pager_overlay/scrolling.rs`。
- 6：`tui/src/resume_picker.rs`、`resume_picker/archive.rs`，恢复工作目录选择链路及测试。
- 7：`tui/src/app_backtrack.rs`、历史选择与分支输入恢复链路及测试。
- 9：`tui/src/notifications/{mod,osc9,bel}.rs`、`tui.rs`、
  `tui/src/chatwidget/notifications.rs` 的焦点、策略、触发链路及测试。

## 必要差异与验证边界

- 保留 Python/Prompt Toolkit 架构，不复制官方服务。命令分类采用保守解析，
  不能确定的命令显示为普通执行，不强行归为读取/搜索。
- 本地文件搜索使用与参考实现一致的 ignore/nucleo 依赖规则。当前原生安装包只
  在本机 macOS arm64 验证；缺少原生 helper 时保留有界 rg 降级，其排序/目录
  候选行为不宣称完全一致。已安装 helper 出错时明确报错，不静默换算法。
- 图片解码使用受管子进程隔离，超时、取消和草稿变更后的过期结果均处理。
  单图20 MiB/3200万像素、最多8张；未读取真实用户剪贴板。
- 富输入历史限同次运行；跨进程普通输入历史只恢复文字，不把手写图片标记当附件。
  缓存、撤销、队列、图片及突发粘贴有容量限制，细节见 progress。
- 历史显示使用私有临时文件与按行缓存，不复制 Rust 的渲染器。所有活跃行存储
  合计最多512 MiB，单行65536字符；超限或存储失败明确显示可退出错误，源历史
  不删除。关闭窗口即回收临时文件。主屏延迟输出超限改用源历史重绘。
- 会话预览和回溯历史使用有界分页/简化文本显示，不宣称所有像素与 Codex 相同。
- 测试覆盖真实 PTY 协议和本地 fake-model/runtime 链路，不等于人工验证全部
  iTerm2/Terminal/Windows/Linux 外观、系统通知弹窗或第三方供应商行为。
- 未调用付费模型、使用用户密钥、改动 teach.md、提交或推送。

## 最终测试结果

| 测试批次 | 实际结果 |
| --- | --- |
| 最终CLI及相关运行时/工具/存储联合回归（8 workers） | 1233 passed，13.56秒，退出0 |
| 原生文件搜索完整文件（隔离串行，含10001目录） | 9 passed，2.51秒，退出0 |
| 全E2E/真实PTY（8 workers） | 400 passed，409 warnings，267.74秒，退出0 |
| 新构建wheel打包/默认搜索启动 | 6 passed，9.17秒，退出0 |
| 首次全项目unit/integration | 17104 passed、4 failed、8 skipped，679.58秒 |
| 上述失败所在两文件修正/隔离后的完整串行复验 | 24 passed，5.52秒，退出0 |

首次全项目回归不是全绿：三个流式工具测试夹具修正为有效事件顺序及实际显示回调；
大目录搜索在高并发下失败，串行原样通过，最终隔离验收，不放宽生产超时或减少样本。
修正后的CLI联合批次全部通过；没有声称再次全跑17000多项得到单次全绿。
8项条件跳过不计作通过；其中wheel条件测试已单独提供新产物通过。
E2E警告主要为本机Python多线程forkpty弃用提醒，未因此跳过PTY。

ruff check通过；相关1066文件format检查和git diff --check通过。
两个无关既有测试/示例文件的format差异未修改，详见progress。
构建产物位于 `/tmp/corki-cli-acceptance-wheel.GfJWdu/`，未发布。
