# 协议样例

这里仅有人工构造的结构样例。占位文件、ID、哈希、指标和审核结论不对应真实输入、模型输出或工具计算，不能作为课程实验记录。完整办公样例 CSV/TXT 尚待后续阶段制作。

| 文件 | 用途 |
| --- | --- |
| [valid.json](valid.json) | 五类角色载荷及引用一致的合法交接样例 |
| [needs-input.json](needs-input.json) | 缺少部门，结构合法但不能进入 Planner |
| [invalid.json](invalid.json) | 缺项未申报、带阻断问题仍批准、来源路径越界三个非法对象 |

在项目根目录加载 JSON 文本，保留严格类型校验：

```python
import json
from pathlib import Path
from office_agents.schemas import TaskSpec

example = json.loads(Path("examples/contracts/valid.json").read_text())
task = TaskSpec.model_validate_json(json.dumps(example["task"]))
assert task.ready_for_planner
```

Python 直接构造指标时使用 `Decimal`；JSON 用十进制字符串或整数，禁止浮点输入。完整约定、跨对象检查顺序及实现边界见[业务数据协议](../../docs/业务数据协议.md)。
