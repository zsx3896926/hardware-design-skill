# 第七轮报告：原理图排版引擎与真实执行边界

日期：2026-10-24。分支 `feat/sch-layout-engine-round7`，worktree
`C:\Users\jlc\work\hds-round7`，基线 `origin/main` `fe950d3`。

## 1. 本轮目标与实际结论

目标是把“AI 手工猜坐标”改成“测量 → 功能模块 → 程序布局 → 合页 → 受保护写入 →
网表/导图复核”。本轮完成了离线纵向链路、独立质量检查、真实 EDA 测量和稳定身份
绑定；**完整整页重排没有写入成功**，阻塞点是一个库符号的锚点/引脚不在 5-raw 网格，
以及官方回退在重连阶段超时。用户工程 `test` 未被修改。

一句话结论：

- 能证明：同一份 26 器件、14 网络、65 连接的工程，程序可以算出单页 6 模块、
  0 个质量 error 的候选图，并给出与改前图的量化对比。
- 另一并行会话（`jlc-ea`）用 netport + group-move 路径在 scratch 上完成了真实重排；
  本会话对该最终备份做了独立回读：**65/65 引脚网络分区完全一致、gate pass、
  PCB DRC 0**。详见 `docs/round7-peer-verification.md`。
- 不能证明：本会话的 compose 候选已经写进 EDA。该路径仍被 D4 的 off-grid 符号
  阻塞；真实重排来自并行会话，不是本流水线。

## 2. 能力表

| 能力 | 状态 | 验证项目 | 证据 | 限制 |
|---|---|---|---|---|
| 真实几何读取 | DONE | 温度报警板 26 件 | `final/before-list.json`、`before-conn.json`、`before-designators.json` | 仅当前页；非激活页为浅数据 |
| 功能模板展开 | DONE | 6 个功能区覆盖 26/26 件 | `final/model/layout-source.json`、`model-report.json` | 模板按位号；换工程需新计划 |
| 模块内部布局 | DONE（离线） | USB/NTC/阈值/比较/驱动/指示 | `final/planned/zones-geom.json` | 驱动模块含 D4，apply 不可用 |
| 正交连线 | DONE（离线） | 116 段导线、32 个电源/地标记 | `final/planned/pages.json` | 5 处异网 X 交叉，需人工判读 |
| 标签与文字避让 | PARTIAL | 26 个位号 bbox 实测 | `before-designators.json`、`planned-quality.json` | 型号/参数/自由文本未测量 |
| 页面布局与分页 | DONE | 并行会话真实单页 6 框；本会话离线 6 区 | `verify/sch.png`、`final/planned/layout.svg` | 本会话候选未 apply |
| 电气等价 | DONE（独立复核） | 26 器件、65 引脚、14 网分区一致 | `docs/round7-peer-verification.md`、`verify/connectivity.json` | 仅原理图；PCB 另核 |
| 实际 EDA 写入 | DONE（并行会话） | 真实重排、gate pass、PCB DRC 0 | `verify/`、对方 `final-report.md` | 本会话 compose 路径仍 BLOCKED |
| 保存重开 | DONE（对方记录 + 我方导入回读） | 65/65 引脚；26/26 PCB 位置 | 对方 save/reload 日志、`verify` 导入 | 本会话未直接重开对方活工程 |
| 局部返工 | PARTIAL（并行会话） | 模块移动后修重叠/标记 | 对方报告 | 本会话引擎未演示 |
| 官方 autolayout 回退 | BLOCKED | 26 件移动后重连超时 | `evidence/round7/layout` 外部日志 | 留下无效事务，已 `doc reload`，状态弃用 |

## 3. 实际做了什么

### 3.1 代码与测试

新增：

- `design-hardware/scripts/sch_layout_model.py`：真实快照 + 连接 IR + 位号几何 +
  图框 → 校验后的 `layout-plan --zones` 输入；检查覆盖、pin-net、NC、5-grid、
  位号文字、纸张/图签；锚点偏移不合法时归一参考并阻塞。
- `design-hardware/scripts/sch_layout_quality.py`：独立图面质量检查，覆盖页面失衡、
  模块重叠/过近/贴图签/碎片化、文字压符号、导线压字、阅读顺序、标签碎片化。
- `design-hardware/scripts/sch_layout_pipeline.py`：串联 `layout-plan →
  layout-sheet-plan → layout-render → quality`，并检查每个 placement 是否为测量的
  刚体平移；锚点被求解器改动时直接报 `geometry-inconsistent`。
- `design-hardware/assets/layout-modules/temperature-alarm.module-plan.json`：
  6 区功能模板，无绝对坐标。
- `design-hardware/references/schematic-layout-engine.md`：调用方式、数据契约、
  硬门禁、工具陷阱、交付证据。
- `tests/test_sch_layout_model.py`、`test_sch_layout_quality.py`、
  `test_sch_layout_pipeline.py`：12 个测试。

验证：

```text
python -m unittest discover -s tests  → exit 0
新增 12 个测试 → OK
```

### 3.2 真实 EDA 基线

- 工程 `test`（`d71c031469054b24b71c3ace8f045b1d`），页 `P1`
  (`6a8364999a809cb4`)，EDA 4.1.78，easyeda-agent 1.14.0。
- 备份 `evidence/round7/before-round7.epro2`，SHA-256
  `f5f1b33fdd10555c12ee5647864619fabfb1669ad5734a0fff4daa0541bc45e9`。
- 改前 SVG/PNG、`sch list`、connectivity、designator-geometry、sheet-geometry
  均已落盘。
- 用户工程 `test` 全程只读；未写入、未删除、未下单。

### 3.3 离线候选结果

- 6 个功能区：usb / sensor / vref / comp / driver / indicator，
  单页 A4 横向，图签 keep-out `[460,10]-[1160,190]`。
- 候选预览：`final/planned/layout.svg`、`layout-full.png`。
- 质量对比（同一 26 件、同一模块定义）：

| 指标 | 改前 | 候选 | 变化 |
|---|---|---|---|
| error | 5 | 0 | -5 |
| warn | 6 | 2 | -4 |
| 模块框重叠 | 5 对 | 0 | 消除 |
| 页面质量碎片化 | usb/comp 超 200 raw | 0 | 消除 |
| 质心偏移 | x +166.5 | x -183.8 | 方向改变，未消除 |
| 导线总长 | 2310 | 1805 | -505 |
| 异网 X 交叉 | 0 | 5 | +5 |
| 标签/电源重复 | 有 | 有 | 未解决 |

候选仍有 2 个 warning：质心偏向左侧、页面粗网格空置率 77%。这说明“不再重叠”
不等于“版面已最优”；剩余留白与模块内文字仍需人工审图。

### 3.4 实际执行尝试

1. **稳定 ID 绑定（成功）**：在隔离副本上逐件写入
   `otherProperty["EasyEDA Agent Component ID"] = cmp-<位号>`，26/26 成功，
   保存并回读。该属性是 `compose --preserve-instances` 的前置条件。
2. **受保护队列（部分成功）**：在早期 scratch 上 `compose --layout-page` 生成了
   195 步队列，dry-run 通过；删除清单 131 项不含任何器件；26 件为
   `preserveInstance` 移动。执行前现场被另一个会话的 `group-move/prim-delete/
   connect` 改动，门禁按设计拒绝，未写入。
3. **官方 autolayout 回退（失败）**：`--engine official --apply --rewire` 移动了
   26 件，在“删除旧线并重建”阶段连接器超时，文档进入
   `unknown or non-sync transaction`。`doc reload` 恢复后可读到器件已移动、所有
   pin net 为空、组件数从 92 降到 51。该状态弃用，不作为验收。
4. **最终干净副本 compose（被 D4 拒绝）**：重新导入基线、重新绑定 26 件、
   重新测量。6 区单页候选通过离线质量检查；compose 对 D4 报
   `planned component outside sheet or off-grid`。原因是 D4/KT-0805R 的存储锚点为
   224、引脚为 210，引脚相对锚点偏移 -14 raw，不在 5-grid；求解器只能把锚点归一
   到 225，写入时会比计划 pins 偏 1 raw。`sch_layout_pipeline.py` 在 apply 前就
   用 `anchor translation differs from pin translation` 拦住了它。

### 3.5 并行会话的真实重排与独立复核

并行会话 `jlc-ea` 在 `temp-alarm-r7-scratch` 上完成了真实重排，最终备份
`temp-alarm-r7-final.epro2`（387,055 bytes，SHA-256 `E4BD1B9E…7B41A13E`）。
本会话把它导入为 `temp-alarm-r7-verify`（`a5689d21…`），只读核对：

- 26/26 器件身份与封装一致；65/65 引脚网络**分区完全一致**，14 个网；
  改名 `D1_N1→VBUS`、`D1_N3→CC1`、`D1_N4→CC2` 不改变任何分区；6 个 USB 数据脚 NC。
- `sch gate` pass；`layout-lint` 0 overlap / 0 tight / 0 pin-coincidence / 0 off-grid。
- `sch clusters`：本会话逐对统计 24 条 WARN `tight`（gap 6–19 raw，小于默认 20）；
  对方按簇/组聚合报 5 条。两者均无 ERROR，差异是统计口径，不是几何冲突。
- 官方 `layout-score` **60.7 / fair**（无 zone claims）；对方加 zone 认领后复跑为
  **66.7 / fair**（proximity 85.7）。reversed-labels 仍 0，但其 fix 与引脚外法线
  冲突：实测 `R10.1`/`C1.1`/`R14.1` 的 outward rotation 均为 180°（朝左），
  score 却要求 `--direction right`。这是评分器“以核心为参照”的口径问题，不是真实
  缺陷；盲翻会被 `sch connect` 拒绝或把标签穿进器件，因此未执行。
- 本会话 `sch_layout_quality.py`：**0 error / 6 warn / 3 info**（改前 5 error）。
  剩余 warning 是页面上重下轻、粗网格空置率 65.6%、usb/sensor/comp/driver
  四个模块的成员离核心较远（大量 netport），属于风格取舍。
- PCB `pcb drc`：**passed, total 0**；`pcb layout-lint` 92/easy、71/71 焊盘、
  2 crossings、ratsnest 5,632.51 mil。

结论：真实原理图已经显著改善且电气等价。官方评分仍是 fair，但其 reversed-label
fix 与引脚外法线冲突（见 3.5），不执行；剩余是 netport 风格与标题栏造成的留白
取舍，不是电气或几何错误。制造包（Gerber/CPL/BOM）仍 `BLOCKED`，未打样、未下单。

## 4. 为什么没有强行写入

- 任务要求不得为好看改变电气连接、不得整页删除重建、不得手改队列坐标绕过守卫。
- 对 D4 的 1-raw 修正会让锚点落在非 5-grid，compose 直接拒绝；保持原锚点则
  写入后引脚与计划导线差 1 raw。两种都不满足“可复现、可复核”的写入契约。
- 官方 autolayout 已证明在没有模板的情况下会破坏现场；不能把它的半成品当作
  “排版完成”。
- 同一 EDA 窗口存在并发写入；在未取得独占前继续写入会制造无法归因的漂移。

## 5. 未完成项与下一条动作

| 项 | 缺什么 | 为什么未完成 | 已准备 | 下一条具体动作 | 环境 |
|---|---|---|---|---|---|
| 本流水线整页写入 | D4 可用的符号/姿态 | D4 引脚偏移 -14，不在 5-grid | 6 区候选、195 步队列、compose 校验 | 用 `sch rebind-symbol` 为 D4 换一个引脚在 5-grid 的等价 LED 符号（保持封装/脚号/网络），或把驱动模块改为刚体区并单独写入 | EasyEDA 4.1.78 + 隔离副本 |
| 反向 netport 标签 | —（已评估，不执行） | score 的 fix 与引脚外法线冲突：R10.1/C1.1/R14.1 outward=180°，fix 要求 right | `score.json`、`list.json` 引脚旋转 | 维持现状；除非换评分口径或人工审图另有要求，不翻标签 | 不适用 |
| 制造包 | Gerber/drill/CPL/BOM 导出 | CLI 无导出能力，隐藏标签页 BOM 返回 no file | 最终 `.epro2`、BOM 表 | GUI 全在线会话导出并核对层叠/阻焊/板框/位号 | 用户 GUI 会话 |
| 第二项目验证 | 另一结构的低压电路 | 本轮时间用于单项目闭环 | 模块模板与流水线 | 换一个 2–3 模块电路，跑同一 model/pipeline/quality | 离线 + EDA 副本 |
| 局部刚体区 | 驱动区 compositions 的 flags 精确重建 | 当前未实现按区保留既有 flags 的转换 | 驱动区当前几何、solver flag 格式 | 为 driver 生成 rigid zone（placements/wires/flags 原样），重跑 compose | 同上 |
| 局部返工演示 | 一次 apply 成功 | 依赖上两项 | 质量检查与网表 diff 脚本 | apply 后只改一个模块，重跑全量网表与质量对比 | 同上 |
| 文字避让 | 型号/参数/自由文本 bbox | `designator-geometry` 只测位号 | 位号 bbox 26/26 | 补官方属性 bbox 导出或 SVG 文本测量 | EDA + 官方 SVG |
| 并发隔离 | 单写入者协议 | 本轮另一会话在写同一副本 | 审计日志、备份、坐标 | 写入前 `audit tail` + 项目级锁文件，单会话独占 | 多会话环境 |

## 6. 工具陷阱（本轮实测，已写入参考文档）

1. `compose --preserve-instances` 要求 `EasyEDA Agent Component ID`；没有就拒绝。
2. `sch list --include-page-primitives` 返回 11 类，compose 守卫只接受 10 类（多
   `pins`）；需保留原快照并做可追溯归一化。
3. `sch designators verify` 的整页场景哈希会被自动保存/并发写入打漂移；逐件回读更稳。
4. `sch modify` 失败可能触发连接器“write/document transition is being verified”，
   后续读也超时；等待或 `web reload` 恢复，恢复后必须重读现场。
5. `web reload`/保存可能导致 EDA 自动改名（`D1_N1`→`VBUS` 等）；跨会话复用旧 JSON
   前先 diff 网名。
6. `layout-score` 会给失衡页面 94 分；独立质量检查才能报出模块重叠、质心偏移和
   碎片化。
7. `layout-sheet-plan --flow compact` 在本机实测输出过重叠框；必须全局坐标复核。
8. 官方 `autolayout --engine official --rewire` 在 4.1.78 上重连超时，会把文档留在
   无效事务；只能 `doc reload`，不能当成功。

## 7. 提交与证据

- 分支：`feat/sch-layout-engine-round7`；基线 `fe950d3`；提交 `6fcd723`；
  PR https://github.com/zsx3896926/hardware-design-skill/pull/11；CI 结果见 PR。
- 证据目录（仓库外）：
  `C:\Users\jlc\work\temp-alarm-20261024\evidence\round7\final\`（本会话）与
  `...\evidence\round7\verify\`（并行会话最终备份的独立复核）。
- 并行会话最终备份：`evidence/round7/temp-alarm-r7-final.epro2`，387,055 bytes，
  SHA-256 `E4BD1B9E80E39FBF87D9F38FD34EA5D9C7341DE7B5DFC4A0DE931CED7B41A13E`。
- 仓库内证据索引：`docs/evidence/round7/README.md`；独立复核：
  `docs/round7-peer-verification.md`。
- 用户工程 `test`：只读，未改变；最终 EDA 窗口会切回 `test`。
- 隔离副本：`temp-alarm-r7-scratch`（另一会话使用）、
  `temp-alarm-r7-layout`（官方回退失败，弃用）、`temp-alarm-r7-final`
  （干净基线 + 稳定 ID 绑定，未重排）。
