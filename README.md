# 多智能体智能办公汇报系统

软件体系结构课程实验。三人协作开发五个角色，由 LangGraph 驱动需求解析、大纲、资料统计、写作和审核。交付包内含两个真实模型生成的季度报告、合成输入、逐轮产物和界面截图。

## 启动工作台

在仓库根目录使用 Python 3.11。首次安装依赖需要网络，安装完成后可以离线查看已记录的实验。

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pip install --no-deps -e .
python -m streamlit run app.py --server.address 127.0.0.1
```

打开终端打印的本机地址。首次进入“查看实验记录”无需模型密钥。已有虚拟环境时，只需激活环境并运行最后一条命令。Windows PowerShell 使用 `.venv\Scripts\Activate.ps1` 激活。

## 三分钟演示

1. 选“技术部 · 第二季度”，核对项目完成率 **60.00%**、出勤率 **96.00%** 和 **4 项成果**。在“来源核对”展开完成率，再打开 projects.csv 对照 P01–P05。
2. 切换“行政部 · 第一季度”。从报告中核对 **17/20 = 85.00%**，再看 notes.txt 第 3 行，说明访客预约回执延迟这句话的出处。
3. 选审核返工实验，在“协作与审核”中对照第零轮和第一轮，观察错误的声明被退回 Writer 后如何复审。回到“业务汇报”下载文件。其他场景演示资料缺失、审核上限与服务超时。

## 生成一份新报告

首次配置可执行 `cp -n .env.example .env`，在本地 .env 填写 MODEL_API_KEY，使用与 Chat Completions、JSON mode 和原生函数调用兼容的服务。本项目的真实记录使用 OpenCode Go 的 deepseek-v4.1-flash。打开“发起真实运行”，选择合成资料、编辑需求并提交。模型调用可能产生费用；每次运行最多 30 次，实际用量见事件日志。本机配置说明见 [M1 实施记录](docs/M1实施记录.md)。

```bash
python -m office_agents.workflow tech-q2
python -m office_agents.workflow admin-q1
python -m office_agents.workflow admin-q1 --inject
python -m office_agents.role_probe
python -m pytest
```

前两条分别生成报告。第三条在初稿的一项指标上加一，执行完整的审核和返工实验。第四条用固定的行政部上游产物逐个验证真实角色。最后一条执行离线测试。除测试外，这些命令都会调用配置的模型。运行目录位于 runs/，其下的 summary.json 给出终态、耗时、调用数和产物索引。只有 succeeded 的运行提供 report.md。硬刷新会重置临时界面会话，原始记录仍在磁盘。

## 交付材料

| 阅读目的 | 入口 |
| --- | --- |
| 交作业、打印或离线阅读 | [课程交付文档 PDF](docs/课程交付文档.pdf) |
| 项目目标、三人任务与验收 | [项目任务书](docs/项目任务书.md) |
| 搭建、运行结果、测试与限制 | [课程实验报告](docs/课程实验报告.md) |
| 组件、部署、时序、架构取舍与 MAS 对照 | [软件体系结构分析](docs/软件体系结构分析.md) |
| 原始输入、截图、日志和两份输出 | [实验证据索引](evidence/index.md) |
| 业务数据与接口 | [项目规范](docs/项目规范.md) · [业务数据协议](docs/业务数据协议.md) |
| 开发依据与协作 | [架构设计基线](docs/架构设计.md) · [实施计划](docs/实验实施计划.md) · [合作准则](docs/合作准则.md) |

原始[课程实验指导书](软件体系结构（研究生）实验指导书：基于OpenClaw的多AI%20Agent智能办公协作系统开发.docx)保留。课程采用三人组、开源框架自选。本人贡献、组员复现和评审由实际参加者签认。
