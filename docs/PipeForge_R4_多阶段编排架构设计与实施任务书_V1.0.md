# PipeForge R4 多阶段编排架构设计与实施任务书 V1.0

> 版本 V1.0 · 2026-11-22 · 前置：R3（v0.4.0+稳定性补丁 b4ebefb，45/45）
> 决策依据：R3 评审最终路线（R3.6 边界清理 → R4 → GS 十阶段 Mock → R5 真 Worker → Colab 生产链）

---

## 一、定位与不变量

**PipeForge 是管理长耗时、可中断、可恢复、产物可追溯的 AI 生产任务框架，不是 3D 资产流水线。**
GS 是第一个业务验证场景，不是架构边界。

R4 结束时必须仍然成立的不变量：

1. PF 内部零 GS 概念：无阶段名硬编码、无 Hunyuan3D/Blender/Godot import、无游戏资产 schema；
2. 依赖方向单向：GS adapters → PF；Colab/Hunyuan 是某个 Stage Worker 的实现细节，PF 不感知
   执行位置（本地 / Colab / 任意 GPU 机）；
3. Worker 契约不变：三段式、无协议副作用、不碰 attempt/状态/发布标记；
4. R3 的四场景一致性标准（正常/重试/中断恢复/重复扫描）在多阶段下继续成立并成为回归基线。

## 二、四个设计契约（先锁定，再编码）

### 契约 1 · Stage（通用阶段描述）

```python
@dataclass(frozen=True)
class Stage:
    key: str                      # 任务 schema 里的阶段键（GS 十阶段 or 任意业务键）
    worker: BaseWorker
    depends_on: tuple[str, ...] = ()   # 同一任务内其他阶段键；默认线性前驱
    optional: bool = False        # 允许 SKIPPED 通过
```

规则：
- PF 校验依赖图（无环、键存在、无重复）；不解释键的业务含义；
- `depends_on` 为空 → 接入任务阶段键的默认线性序（对齐 R3 `assert_stage_order` 语义）；
- 阶段键必须存在于该任务 schema 的 stages 定义中，构造期校验，运行期不再猜。

### 契约 2 · 阶段级 Checkpoint 与恢复

验收核心：执行到第 6 阶段失败 →
- 前 5 阶段：checkpoint 完好，重启动 Pipeline 后**不重复执行**；
- 第 6 阶段：按错误分类决定重试或终止（沿用 R3 分类：可重试/不可重试/崩溃）；
- 第 7+ 阶段：保持未执行；
- 恢复 = 新进程重新调 `Pipeline.run`，从第一个非 COMPLETED/SKIPPED 阶段继续。

实现约束：完全复用 R3 Runner（Pipeline 不自己执行 Worker），阶段恢复语义即 R3 幂等跳过
语义的自然延伸，不另造第二套。

### 契约 3 · 阶段输入输出（Artifact 引用）

- Worker 通过 `WorkerContext.upstream(key)` 获取上游阶段产物引用（path_rel + sha256，
  取自该上游阶段 checkpoint_data），**不得**直接读其他 Worker 的内部变量；
- `WorkerContext.artifacts_of(stage_key)` 全量查询；引用不存在 → validate_input 抛错
  （INPUT_INVALID，不可重试）；
- Runner 已有交叉校验（worker 结果 vs 持久化检查点）继续生效。

### 契约 4 · 发布协议（EXECUTED → VALIDATED → PUBLISHED）

```python
@dataclass
class Publisher:
    storage: Storage
    def validate(self, task, artifacts, *, validator=None) -> PublishReport   # VALIDATED
    def publish(self, task, artifacts, target_rel) -> PublishReport           # PUBLISHED
```

- Pipeline 执行成功只代表 **EXECUTED**；发布独立调用、独立状态；
- 幂等：目标已存在 → 拒绝（FileExistsError），已有正式版零损伤（复用 R2 八步）；
- 发布失败 ≠ Worker 失败：PUBLISH_FAILED 只标记发布环节，**绝不触发昂贵上游重跑**；
- PUBLISHED 标记最后写，含 manifest 指纹；发布记录可追溯（PublishReport 落 sidecar 或
  版本目录，R4.4 定稿）。

## 三、Pipeline API（对齐评审架构图）

```python
class Pipeline:
    def __init__(self, storage, stages: list[Stage], repository=None,
                 checkpoints=None, publisher: Publisher | None = None)
    def plan(self, task) -> list[str]              # 拓扑序（缓存，供检查/演示）
    def run(self, task_or_id) -> PipelineReport     # 逐阶段委托 Runner
```

```python
@dataclass
class PipelineReport:
    task_id: str
    outcome: str                # EXECUTED / PARTIAL / FAILED / SKIPPED_ALL
    stage_reports: list[RunReport]
    executed_stages: list[str]
    resumed_from: str | None    # 恢复起点（本次跳过数 > 0 时）
```

失败传播语义（评审五分法的落地）：

| 失败类别 | 阶段处置 | Pipeline 处置 | 发布 |
|---|---|---|---|
| 可重试错误 | RETRY_WAIT（R3 重试账目） | 当次 PARTIAL 退出 | 不进入 |
| 不可重试错误 | FAILED | 当次 FAILED 退出 | 不进入 |
| 用户取消 | CANCELLED（新增转换边，见 §五） | CANCELLED 退出 | 不进入 |
| 上游依赖失败 | 保持未执行 | FAILED 退出 | 不进入 |
| 发布失败 | 阶段不动 | EXECUTED 保持，发布报告 FAILED | 幂等可重发 |

## 四、里程碑

| # | 交付物 | 验收 | 状态 |
|---|---|---|---|
| R4.1 | Stage/Pipeline/PipelineReport/PublishReport 定义 | 构造期依赖图校验测试；零 GS 字段 | 待做 |
| R4.2 | 多阶段执行（拓扑序委托 Runner） | 按依赖顺序执行；钻石依赖图测试 | 待做 |
| R4.3 | 阶段恢复 | 中断后重启跳过已完成阶段（新进程测试）；第 N 阶段失败后续不动 | 待做 |
| R4.4 | Publisher（validate/publish 分离） | 发布幂等；发布失败不触发上游重跑；PUBLISHED 最后写 | 待做 |
| R4.5 | R4 测试集 | 失败/重试/恢复/取消/发布幂等全绿；R3 45 条回归全绿 | 待做 |
| R4.6 | GS 十阶段 Mock Demo | 真实 GS Task（协议 schema 十阶段）+ Mock Worker ×10 全链通过 | 待做 |

## 五、协议层最小增量

- 任务状态机新增合法边：`RUNNING → CANCELLED`（已有）之外，补 `REVIEW → CANCELLED`（用户
  在人工确认环节取消）；其余不动；
- schema 零改动（stages 双形态已兼容 SKIPPED）；
- versions.json / 文档登记 schema 2.0 不变——R4 是框架能力，不是协议变更。

## 六、测试矩阵（R4.5 定稿清单）

| 类 | 用例 |
|---|---|
| 图校验 | 环拒绝 / 未知键拒绝 / 重复键拒绝 / 钻石图合法 |
| 执行 | 线性序 / 钻石并行序（拓扑有效即可）/ optional 阶段 SKIPPED 通过 |
| 恢复 | 6 阶段中断于 4 → 重启跳过 1-3 执行 4-6 / 新进程恢复 / attempts 不重复 |
| 失败传播 | 可重试→PARTIAL / 不可重试→FAILED / 取消→CANCELLED / 发布失败≠执行失败 |
| 发布 | 双发布拒绝 / 产物校验失败不发布 / PUBLISHED 幂等标记 |
| 回归 | R3 全量 45 条不退化 |

## 七、风险与对策

| 风险 | 对策 |
|---|---|
| Pipeline 退化成"Runner 外面套循环" | Pipeline 只做编排不做执行；所有阶段语义仍由 Runner 产生 |
| 阶段间产物引用引入隐式耦合 | 只经 checkpoint_data 引用；Worker 间零直接依赖 |
| 发布状态与执行状态混淆 | 两个状态域 + PublishReport 独立落账；命名强制区分 |
| Mock 全绿但真 Worker 行为不同 | R4.6 用真实协议 schema + 真实参考图；R5 前置验收 |
| 十阶段顺序硬编码诱惑 | Stage.depends_on 显式声明；plan() 可视化供人审 |

## 八、变更记录

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-11-22 | V1.0 | 首版：四契约（Stage/Checkpoint 恢复/Artifact 引用/发布三态）；Pipeline API 与失败传播五分表；R4.1–R4.6 里程碑；测试矩阵；风险对策 |
