# 原理图排版引擎：测量 → 模块布局 → 合页 → 写入 → 复核

本文件用于“把已有电气设计画清楚”的任务：不改拓扑、不换器件、不删网络，只重排器件、导线和标签。它描述一条已经在本机 EasyEDA 4.1.78 + easyeda-agent 1.14.0 上跑通过的流水线，以及每个环节的硬门禁、证据和已知限制。

目标不是生成一张“分数高”的图，而是让同一个人能顺着主信号链读完、能定位每个连接、能继续修改。分数、截图和 DRC 都只是其中一项证据。

## 1. 先判断任务类型

| 用户说 | 类型 | 默认动作 |
|---|---|---|
| “这张图太乱，帮我画清楚，连接不要变” | 全页重排 | 读现场 → 功能分区 → 局部求解 → 合页 → 写入 → 全量网表复核 |
| “把这一块挪一下 / 这里太挤” | 局部返工 | 冻结其他区，只重算受影响模块；不整页重排 |
| “新画一个电路” | 新建 | 先完成电气设计，再走同一条排版流水线；不要把排版当成设计 |
| “检查一下排版” | 只读 | 跑质量检查与官方 lint/score，输出问题清单，不写入 |

只有用户明确要求改图时才进入写入步骤。用户只给截图、没有工程时，只能做离线候选，并明确“该工程尚未执行”。

## 2. 流水线总览

```text
冻结现场
  → 功能模块计划（角色/核心/成员/网络策略）
  → sch_layout_model.py 构建并校验测量输入
  → sch layout-plan --zones            区内布局与连线
  → sch layout-sheet-plan              合页与图签避让
  → sch layout-render + 质量检查       候选复核
  → sch compose --layout-page          受保护 Apply 队列
  → sch apply --dry-run / --yes        写入
  → save → reopen → 网表 diff → gate → 导图 → 局部返工
```

职责分工：Agent 决定功能分区、阅读顺序、哪些网络直连或打标签；求解器计算坐标、避让和路径；质量检查器独立报告可读性问题；EDA 执行器只做受控写入和回读。

## 3. 冻结现场

每次重排前先保存同一时刻的完整证据。缺少任一项都不要开始写入。

```bash
easyeda doc ls --project <project>
easyeda sch sheet-geometry --project <project> --doc <page-uuid> --json
easyeda sch sheet-geometry --from-svg <page.svg> --json      # 精确内框/图签
easyeda sch list --project <project> --page <page-uuid> \
  --include-device-identity --include-bbox --include-pins \
  --include-wires --include-page-primitives > page-before.json
easyeda sch connectivity --project <project> --page <page-uuid> > connectivity-before.json
easyeda sch designator-geometry --project <project> --doc <page-uuid> --out designators-before.json
easyeda sch check --project <project> --page <page-uuid> --json > check-before.json
easyeda sch export-image --project <project> --page <page-uuid> --format svg --out before.svg
easyeda sch export-image --project <project> --page <page-uuid> --format png --out before.png
```

工程级的备份优先用 `project export --window <id> --project-uuid <uuid> --out <new.epro2>`；它不覆盖已有文件。记录 SHA-256。

**并发规则**：同一个工程副本同一时间只允许一个写入者。开工前查 `easyeda audit tail`，确认没有其他会话正在写；发现漂移立即停写，不要用 `--resume` 跳过守卫。

## 4. 功能模块计划

模块计划只描述相对关系和画法，不写绝对坐标。示例见
[温度报警模块计划](../assets/layout-modules/temperature-alarm.module-plan.json)。

```json
{
  "schemaVersion": 1,
  "defaultNetPolicies": {"+5V": "local_power", "GND": "local_ground"},
  "readingOrder": ["usb", "sensor", "vref", "comp", "driver", "indicator"],
  "zones": [
    {"id": "comp", "title": "Comparator & hysteresis", "core": "U1",
     "members": ["U1", "R13", "R14", "R15", "C3"],
     "netPolicies": {"OUT": "direct", "GATE": "module_port",
                     "NODE": "module_port", "VREF": "module_port"}}
  ]
}
```

规则：

- `members` 用位号，覆盖页内每一个器件，恰好一次。核心必须在成员内。
- 外围绕核心的引脚组织；不要把“所有电阻”单独成区。
- 同一区内的短信号默认 `direct`；跨区信号 `module_port`；电源/地 `local_power`/`local_ground`。
- 不要把跨区网络写成 `direct` 再靠标签补连接。
- 一个新的功能模板至少要在两个几何不同的真实符号/配置上验证；不能适配时明确回退，不硬塞坐标。

## 5. 构建并校验测量输入

```bash
python design-hardware/scripts/sch_layout_model.py \
  --snapshot page-before.json --connectivity connectivity-before.json \
  --designators designators-before.json --sheet sheet.json \
  --module-plan plan.json --out-dir out/model
```

输出：

| 文件 | 内容 |
|---|---|
| `layout-source.json` | `sch layout-plan --zones` 的输入；器件用连接 IR 的 `cmp-*` 稳定 ID |
| `quality-input.json` | 质量检查输入（页面矩形、位号 bbox、导线、模块） |
| `model-report.json` | 覆盖、阻塞、警告、输入哈希、纸张来源 |

`status=ready` 才继续。`blocked` 时逐条处理，不能把 blocker 当 warning。

本脚本强制检查：

- 快照与连接 IR 的位号集合一致；每个器件的每个已连引脚在 IR 中有对应网。
- 空网引脚必须显式 NC 或 unconnected；NC 引脚不能有网。
- 每个器件恰好属于一个区；不存在未分配/重复成员。
- 位号文字 bbox 逐件实测；缺失只警告，但最终验收不能声称文字已验证。
- 引脚坐标在 5-raw 网格；锚点与引脚偏移不在网格时，只把锚点参考归一到最近合法网格，并同时给出 `anchor_requires_review` 阻塞。**不要静默改坐标**。
- 纸张内框与图签 keep-out 来自官方 SVG，不用纸张外框冒充内框。

## 6. 区内布局与连线

```bash
easyeda sch layout-plan --zones --from out/model/layout-source.json \
  --out out/zones-geom.json --report out/layout-report.json
```

- `spacing` 默认 10 raw（0.1 inch）。更小的值只用于确实放不下时，并在报告中记录；不要用缩小字体解决拥挤。
- `maxCandidates` 是每个区的共享搜索预算。失败报告会给出最后冲突、阻挡对象和预算状态；有限预算失败不等于全局无解。
- 失败先改源数据/归属/策略/姿态，再重算。不要手改输出坐标或伪造导线。
- `direct` 网络必须形成真实线树；`module_port` 优先直连，无法安全合并时才在边界命名。不能因为路由失败就把所有网络改成标签。
- 区内结果以核心为局部原点；不要把它当页面坐标，也不要二次平移。

## 7. 合页与图签

把 `zones-geom.json` 加上纸张证据：

```json
{
  "schemaVersion": 1,
  "spacing": 10,
  "zones": [ ... ],
  "sheet": {
    "bounds": {"minX": 0, "minY": 0, "maxX": 1170, "maxY": 825},
    "border": {"minX": 10, "minY": 10, "maxX": 1160, "maxY": 815},
    "keepouts": [{"minX": 460, "minY": 10, "maxX": 1160, "maxY": 190}],
    "padding": 10, "gap": 10, "flow": "z"
  }
}
```

```bash
easyeda sch layout-sheet-plan --from render-input.json --out pages.json --flow z
```

- 小电路优先一页；不要固定三页。先调模块顺序/间距/核心选择，再考虑分页。
- `flow:z` 是确定性的从左到右、按行顶齐；它不会回填短框下的空洞。
- `flow:compact` 可能给出更少的页数，但本机 1.14.0 实测出现过**框重叠/越界**的候选；必须逐框用全局坐标复核，不能只看 `pages=1`。
- 图签是硬 keep-out；模块框、导线、标记、文字都不能侵入。
- 一页放不下时，优先调整模块内部几何、阅读顺序、间距，再分页；不要放大纸张掩盖布局失败。

## 8. 候选复核（离线）

```bash
easyeda sch layout-render --from page.json --out layout.svg
python design-hardware/scripts/sch_layout_quality.py planned-quality-input.json
```

`layout-render` 输出的是简化符号的离线预览，不是官方导图，也不代表已写入。必须看图，不能只看 JSON。

质量检查器报告具名问题，不做单一“美观分”：

| 代码 | 含义 |
|---|---|
| `page_imbalance`, `mass_asymmetry`, `centroid_offset` | 半页空、器件挤在一侧 |
| `sparse_page`, `low_page_utilization`, `crowded_page` | 页面利用率异常 |
| `module_overlap`, `module_too_close`, `module_near_title_block` | 模块框关系 |
| `module_fragmented` | 模块成员离核心过远（例如 USB 接口孤立） |
| `text_over_symbol`, `wire_over_text`, `missing_text_bbox` | 文字可读性 |
| `symbol_overlap`, `component_over_title_block`, `component_outside_page` | 几何硬冲突 |
| `reading_order_mismatch` | 声明的阅读顺序与页面 Z 顺序不一致（提示，不是错误） |

官方 `layout-lint`/`layout-score` 仍然要跑，但它们不检查页面均衡、模块框架和文字遮挡。真实反例：2026-10-24 温度报警板 `layout-score=94 / excellent`，同时左半页空白、右半页模块互相重叠、USB 接口孤立。**不得用其中一个分数代替看图**。

## 9. 写入：受保护队列

当前推荐路径是 `compose --layout-page` + `--replace --preserve-instances`。它会：

1. 核对整页器件实例、位号、库身份、引脚网络/NC。
2. 只清除授权范围内的导线/标记/页面图形，保留器件实例。
3. 按选定页面平移器件，重画导线和标记。
4. 每步断言，失败即停；不自动回滚已成功的写。

```bash
easyeda sch compose --from composition.json --layout-page page.json \
  --out compose-plan.json --before page-before.json \
  --replace --preserve-instances --playbook apply.json
easyeda sch apply apply.json --dry-run
easyeda sch apply apply.json --yes
```

### 9.1 稳定 ID 绑定（preserve-instances 的前置条件）

`preserve-instances` 要求每个 part 的 `otherProperty` 中有：

```text
"EasyEDA Agent Component ID": "cmp-<位号>"
```

手工放置的既有工程通常没有。用官方绑定流程或逐件 `sch modify` 补齐：

```bash
easyeda sch designators plan connectivity.json --before page-before.json --out bind.json
easyeda sch apply bind.json --dry-run
easyeda sch apply bind.json --yes
```

若官方队列因场景哈希漂移拒绝，逐件写入并每次回读；不要把 `--resume/--from/--to` 当绕过守卫的手段：

```bash
easyeda sch modify --project <project> --doc <page> --id <primitiveId> \
  --patch '{"customAttributes":{"EasyEDA Agent Component ID":"cmp-R12"}}'
```

绑定只增加自定义属性，不改位号、型号、封装、网络或 PCB 绑定。写后必须回读 26/26。

### 9.2 写入前快照归一化

v1.14.0 的 `compose` 守卫按“恰好 10 类 pagePrimitive”校验，而 `sch list --include-page-primitives` 还会返回 `pins` 类。直接传入会报 `incomplete class inventory`。处理方式：

- 保留原始快照不动；
- 复制一份，删除 `pagePrimitives.pins`（组件记录里仍有完整 pins）；
- 另存说明 `removedClass/removedCount/reason`；
- 用副本做 compose 的 `--before`，并在报告中记录这是快照归一化，不是删证据。

### 9.3 锚点不在 5-grid 的符号

某些库符号的引脚相对锚点偏移不是 5 的倍数（实测 D4/KT-0805R：锚点 224，引脚 210，偏移 -14）。求解器会拒绝该器件。当前处理：

- 在测量副本中把锚点参考归一到最近合法网格，记录原始/归一值；
- 标记 `anchor_requires_review`，不允许声称 apply 已验证；
- 写入前用 compose 的引脚断言核对实际平移；若 1 raw 级不一致，把该模块回退为**刚体移动**（`sch group-move`），不要手改队列坐标。
- 更彻底的修复是换用引脚在网格上的等价符号；这属于器件/库变更，需单独评估，不能为排版擅自换料。

## 10. 写入后复核

```bash
easyeda sch save --project <project> --doc <page>
easyeda sch connectivity --project <project> --page <page> > connectivity-after.json
easyeda sch gate --project <project>
easyeda sch export-image --project <project> --page <page> --format svg --out after.svg
easyeda sch export-image --project <project> --page <page> --format png --out after.png
```

硬验收：

- 完整网表一致：器件身份/增删、引脚增删、`pin→net` 映射、网络分组全部一致，不能只看 `pin_net_changed=0`。
- 器件实例保留：位号、库身份、`uniqueId`、PCB 绑定、BOM 属性不变。
- `save` 后关闭/重开再读一致；页面 UUID、工程 UUID 未漂移。
- `gate` 的 `blocked` 不等于通过；未执行的阶段单列。
- 前后图必须来自同一最终状态；截图不能早于最后一次写入。

软验收：

- 质量检查的 error 清零；warning 逐条解释（保留/修复）。
- 导图在正常阅读比例下可读；主信号链可追踪；标签没有把简单拓扑拆碎。
- 与改前相比：模块框不再重叠、页面均衡改善、标签/导线没有明显膨胀。

## 11. 局部返工

发现问题时不要整页重排：

1. 定位模块/器件/网络的稳定 ID。
2. 冻结未受影响模块。
3. 只改该区的成员、核心、网络策略或姿态。
4. 重算该区 → 重新合页 → 重新生成队列 → 全量网表复核。
5. 更新导图与质量对比。

停止条件：合法且可读后停止；连续多轮无改善时报告主要冲突和最好候选，不把“达到次数上限”当通过。

## 12. 已知限制与工具陷阱（实测）

- **并发写入**：多个会话可同时驱动同一个 EDA 窗口。审计日志出现非本会话的 `group-move/prim-delete/connect` 时，现场已不可信；停写、协调、换独占副本。
- **写锁**：`sch modify` 失败后连接器可能进入“write/document transition is being verified”，后续读也超时。等待或 `web reload --project <uuid> --doc <uuid>` 恢复；恢复后重新读现场，不复用旧队列。
- **网络自动改名**：`web reload`/保存后，EDA 可能把自动网名从 `D1_N1` 改成 `VBUS` 等。模块计划的网名要与新鲜 IR 对齐；跨会话复用旧 JSON 前先 diff 网名。
- **场景哈希漂移**：官方 `designators verify` 对整页场景做哈希；页面有任何自动保存/属性变化都会拒绝。逐件回读比整页哈希更适合有并发/自动保存的环境。
- **`layout-score` 不是可读性门**：它可以给失衡页面 94 分；必须跑独立质量检查并看图。
- **`layout-score` 的 reversed-label fix 不能照抄**：它按“模块核心方向”判断，不按宿主引脚外法线。实测 `R10.1`/`C1.1`/`R14.1` 的 outward rotation 均为 180°（朝左），fix 却要求 `--direction right`；照做会被 `sch connect` 拒绝或把端口穿进器件。使用前先核对 `pins[].rotation`。
- **`sch clusters` 有逐对/分组两种计数**：同一页可报 24 条逐对 `tight` 或 5 条按簇聚合；都没有 ERROR 时不要当成两套矛盾结论，先统一命令与聚合口径。
- **`layout-render` 是简化预览**：只验证几何转译，不验证官方符号、字体、电气或现场。
- **`layout-sheet-plan` 的 `flow:compact`**：可能输出框重叠；逐框全局坐标复核。
- **文本测量只覆盖位号**：`designator-geometry` 不测型号/参数/自由文字；这些文字的遮挡仍是缺测项，不能声称已验证。
- **PCB 不受益于原理图排版**：原理图的模块邻近不代表 PCB 布局/回流；PCB 另行验收。

## 13. 最小交付证据

一次“排版完成”的交付至少包含：

1. 改前/改后的 `sch list`、`connectivity`、`designator-geometry`、`sheet-geometry` 原始 JSON 与哈希。
2. 模块计划、`layout-source.json`、`zones-geom.json`、`pages.json`、`layout.svg`。
3. 质量检查的前后 JSON 与对比；`layout-lint`、`layout-score`、`gate` 结果。
4. compose 队列、dry-run 输出、apply journal、save/reopen 回读。
5. 前后 SVG/PNG，且注明“离线预览”或“官方导出”。
6. 未完成/未验证项，逐条标 `NOT RUN`/`BLOCKED`。

没有真实工程时，只能交付离线候选和复现命令，不能写“已改善”。
