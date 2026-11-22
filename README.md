# PipeForge

> 面向 AI 生成与数据处理场景的**可恢复生产管线框架**：统一任务协议、阶段状态管理、
断点恢复、Worker 执行接口、质量门禁与产物发布机制。

## 定位与边界

第一版只解决一个核心问题：**让一个生产任务从提交、执行、失败恢复，到质量验证和
产物交付，具备统一协议和可追踪性。**

它不是 AI Agent 框架、分布式工作流引擎或通用任务调度平台。

**业务无关约束**：本包不出现任何业务依赖（Hunyuan3D/Blender/Godot/游戏资产 Profile）。
框架只定义接口与执行机制，业务侧实现 Adapter 接入。

## 当前能力（0.1.0）

| 能力 | 状态 |
|---|---|
| Task / Manifest Schema（draft-07） | ✅ 已迁移 + 双形态 stages（初始空/执行后完整含 checkpoint_data） |
| protocol.py（ID/状态机/七项检查点/Hash/发布校验） | ✅ 已迁移，7 项测试全过 |
| TaskRepository / Checkpoint / QueueScanner / Storage 接口 | 🔜 R1 |
| BaseWorker / WorkerResult | 🔜 R2 |

## 安装（开发期）

```bash
pip install -e .        # 基础（纯标准库，零强制依赖）
pip install -e ".[test]"  # 含 jsonschema 完整校验
```

## 快速验证

```bash
python tests/_run_all.py   # 7 tests, 0 failures
```

## 版本规则（框架版本 ≠ 协议版本）

| 对象 | 当前 | 职责 |
|---|---|---|
| PipeForge 包版本 | 0.1.0 | 框架代码兼容性 |
| Task Schema | 2.0 | 任务协议兼容性 |
| Manifest Schema | 2.0 | 产物清单兼容性 |

## 首个业务接入方

`D:\Gaming Studio\AssetFactory`（3D 美术资产生产）——通过 Adapter 接入，
Profile 与 Blender Worker 保留业务侧。

## 路线

- **R0** ✅ 独立仓库 + 协议迁移（本提交）
- **R1** 任务内核：Storage 接口 → LocalStorage → TaskRepository / StateMachine 薄封装 / CheckpointManager / QueueScanner（tempdir 测试）
- **R2** Gaming Studio 接入：BaseWorker + 香炉样本真实任务跑通 提交→执行→Checkpoint→恢复→发布