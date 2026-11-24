# 实验证据索引

只记录实际保存的证据。未审核的本机文件位于忽略提交的 `evidence/raw/`，不因文件存在自动认定实验通过。

| 编号 | 内容 | 当前状态 |
| --- | --- | --- |
| S01 | 环境、依赖和服务启动原始截图 | 未采集：电脑操作工具禁止访问 Terminal；业务界面服务尚未实现 |
| S02 | 真实文本、结构化输出与工具往返原始截图 | 三项真实验证已通过；原始终端截图待人工采集 |

## M1 真实协议记录

运行编号：`m1-20261028T121831Z-29ae3bc4`。模式：`live`。模型：`deepseek-v4.1-flash`。用例：T01 的文本、JSON mode 和工具往返部分；未执行受控注入。代码版本：`af7e65185477da4a55fdb2a61df676e177835dc6`。

| 文件 | 内容 | SHA-256 |
| --- | --- | --- |
| [manifest.json](m1/m1-20261028T121831Z-29ae3bc4/manifest.json) | 环境、公开配置、源码哈希、请求上限 | `b048f999bb358559b719ca4cbea46150c6a43843ad1c7b5c127d41ca69f49a42` |
| [events.jsonl](m1/m1-20261028T121831Z-29ae3bc4/events.jsonl) | 四次真实请求与响应、实际工具执行，共 12 条事件 | `8ce819f8dc7c5a670437e3a0724ceae968da7cd8c0cf8fcd6f427d4e9e5f3b7b` |
| [summary.json](m1/m1-20261028T121831Z-29ae3bc4/summary.json) | 三项通过；7197 ms；958 tokens；费用未核算 | `140148ca209fe5affb282e9b9d8af17db40ace526d36d650fe838926e35e4bf8` |

以上为已审阅公开副本，与本机原始记录字节一致。没有改写原始日志；公开副本不包含密钥或内部推理内容。它们属于运行记录，不是原始终端截图。

M1 状态、验证结论及限制见 [M1 实施记录](../docs/M1实施记录.md)。后续补充实际截图时填写文件、用例、`run_id`、`live/mock`、模型及说明；当前没有可供索引的截图。

## 2026 年 11 月 24 日课程交付

下列目录均保存原始运行文件的逐字节副本。以运行编号和 manifest 中的源码哈希定位版本。技术部记录从浏览器提交，行政部正常记录来自开发过程中的命令行运行，其 manifest 同时保留当时的 Git 基线与工作树源码哈希。mock 表示请求由测试替身处理。

| 场景 | 模式与结果 | 证据 |
| --- | --- | --- |
| 技术部正常汇报 | live，6 次调用，通过 | [摘要](delivery/tech-q2/summary.json) · [业务报告](delivery/tech-q2/report.md) · [事件](delivery/tech-q2/events.jsonl) |
| 行政部正常汇报 | live，6 次调用，通过 | [摘要](delivery/admin-q1/summary.json) · [业务报告](delivery/admin-q1/report.md) · [事件](delivery/admin-q1/events.jsonl) |
| 五角色独立验证 | live，五项均与固定输入预期匹配 | [摘要](delivery/single-roles/summary.json) · [Checker 指出 18 应改为 17](delivery/single-roles/artifacts/checker-r0.json) |
| 替身受控注入 | mock，第零轮阻断，第一轮通过 | [注入事件](delivery/revision-mock/events.jsonl) · [复审](delivery/revision-mock/artifacts/review-r1.json) |
| 缺少反馈文件 | mock，待补资料 | [运行摘要](delivery/missing/summary.json) |
| 持续退回 | mock，两轮后待人工复核 | [运行摘要](delivery/review-limit/summary.json) |
| 真实连接超时 | live，Manager 两次请求均失败 | [运行摘要](delivery/timeout/summary.json) |
| 早期调试 | live，大纲引入无关章节；另一次审核回复无效 | [Planner 记录](delivery/debug-planner/summary.json) · [Checker 记录](delivery/debug-checker/summary.json) |

| 截图 | 所示内容 | 运行编号 |
| --- | --- | --- |
| [S04](screenshots/S04-input.png) | 技术部表单提交前的需求与资料 | office-0beb5b8a93d5 |
| [S05](screenshots/S05-source.png) | 完成率的口径与来源 | office-0beb5b8a93d5 |
| [S07](screenshots/S07-tech.png) | 技术部汇报的实际下载与核对页面 | office-0beb5b8a93d5 |
| [S08](screenshots/S08-admin.png) | 行政部满意度报告回看 | office-70c18d122edb |
| [S09](screenshots/S09-timeout.png) | 真实模型调用超时的终态回看 | office-06777851ef1a |
| [S10](screenshots/S10-limit.png) | 替身连续退回后待人工复核 | office-22adf140ce80 |

截图由本机 Chromium 实际操作取得。M1 的历史终端截图与当时的 S01、S02 未补录。
