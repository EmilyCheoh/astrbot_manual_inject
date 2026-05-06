# ManualInject - 手动指令注入插件

通过 `mpinject` 指令手动控制向 LLM 请求注入预定义内容。条目在 AstrBot 面板中配置，运行时通过指令激活。

## 两种模式

### once - 一次性注入
注入一次后永久保留在对话上下文中，不会被清除。适用于整个对话窗口都需要的参考信息（如画画时的视觉描述）。

### persistent - 持续注入（默认）
每轮动态注入并清理上一轮注入的内容，直到手动停止。适用于临时性的、需要随时撤回的上下文信息。

## 指令

| 指令 | 说明 |
|------|------|
| `mpinject <条目名>` | 以持续模式激活条目 |
| `mpinject <条目名> once` | 以一次性模式激活条目 |
| `mpinject <条目名> stop` | 停止注入并清理上一轮痕迹 |
| `mpinject list` | 列出所有条目及当前激活状态 |

## 使用示例

假设配置了一个条目：
- 名称：`画画`
- 标签名：`Our Visual Descriptions`
- 内容：Abyss 和 Felis Abyssalis 的视觉描述

```
用户：mpinject 画画 once
插件：「画画」已激活 (once)。

用户：Abyss 我们画画吧
实际发给 LLM 的消息：
  Abyss 我们画画吧

  <Our Visual Descriptions>
  ...视觉描述内容...
  </Our Visual Descriptions>

用户：（后续消息）
视觉描述仍然保留在第一轮的消息历史中，不会被清除。
```

## 与其他插件的共存

| 阶段 | 插件 | Priority |
|------|------|----------|
| 清理 | **ManualInject** | **3** |
| 清理 | FirstWindowInject | 2 |
| 清理 | PromptTags | 1 |
| 清理 | LivingMemory | 0 |
| 注入 | LivingMemory | 0 |
| 注入 | FirstWindowInject | -499 |
| 注入 | PromptTags | -500 |
| 注入 | **ManualInject** | **-501** |

各插件使用独立的标签名称，清理正则不会交叉匹配。

## 配置

在 AstrBot Web 面板中配置，支持最多 10 个条目。每个条目有 4 个字段：

| 字段 | 说明 |
|------|------|
| 条目名称 | `mpinject` 指令中使用的关键词 |
| 标签名称 | 注入时包裹内容的标签名（不能包含 `<` `>` 或换行） |
| 注入内容 | 实际注入的文本 |
| 注入位置 | `user_message_before` / `user_message_after` / `system_prompt` |

## 开发信息

- **作者**: Felis Abyssalis
- **版本**: 1.0.0
- **依赖**: 无额外依赖，仅使用 AstrBot 内置 API
