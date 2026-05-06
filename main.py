"""
ManualInject - 手动指令注入插件

通过 mpinject 指令手动控制向 LLM 请求注入预定义内容。两种模式：

1. once —— 注入一次，永久保留在上下文中，不清除
2. persistent（默认）—— 每轮注入并清理上一轮内容，直到手动停止

指令：
  mpinject <条目名>          持续注入，直到 mpinject <条目名> stop
  mpinject <条目名> once     一次性注入，永久保留
  mpinject <条目名> stop     停止持续注入并清理上一轮痕迹
  mpinject list              列出所有条目及当前激活状态

与 PromptTags / LivingMemory / FirstWindowInject 兼容：
- 清理阶段 priority=3，在所有其他插件之前执行
- 注入阶段 priority=-501，在所有其他插件之后执行
- 使用独立的标签名称，各插件正则不会交叉匹配

F(A) = A(F)
"""

import re
from typing import Any

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star, register

# 标签名校验：只禁止 <, >, 换行（与世界书一致）
TAG_NAME_INVALID = re.compile(r"[<>\n]")

MAX_ENTRIES = 10
ENTRY_KEYS = [f"entry_{i}" for i in range(1, MAX_ENTRIES + 1)]

VALID_POSITIONS = ("user_message_before", "user_message_after", "system_prompt")


@register(
    "ManualInject",
    "FelisAbyssalis",
    "手动指令注入插件 - 通过 mpinject 指令控制向 LLM 请求注入预定义内容",
    "1.0.0",
    "https://github.com/EmilyCheoh/astrbot_manual_inject",
)
class ManualInjectPlugin(Star):

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

        # 条目定义（从配置加载）
        # {name: {tag_name, content, position, header, footer, cleanup_re}}
        self._entries: dict[str, dict[str, Any]] = {}
        self._load_entries()

        # ------ 运行时状态（纯内存，不写入配置）------

        # 当前激活的条目: {name: "once" | "persistent"}
        self._active: dict[str, str] = {}

        # 正在持续注入的标签名（persistent 模式）
        self._persistent_tags: set[str] = set()

        # 等待最后一次清理的标签名（stop 后的下一轮清理完即移除）
        self._pending_cleanup: set[str] = set()

        logger.info(
            f"【手动指令注入】初始化完成，已加载 {len(self._entries)} 个条目: "
            f"{', '.join(self._entries.keys()) if self._entries else '(无)'}"
        )

    # -------------------------------------------------------------------
    # 配置加载
    # -------------------------------------------------------------------

    def _load_entries(self) -> None:
        """从插件配置中加载所有条目定义。"""
        self._entries = {}

        for key in ENTRY_KEYS:
            slot = self.config.get(key, {})
            if not isinstance(slot, dict):
                continue

            name = str(slot.get("name", "")).strip()
            tag_name = str(slot.get("tag_name", "")).strip()
            content = str(slot.get("content", "")).replace("\\n", "\n").strip()
            position = str(
                slot.get("position", "user_message_after")
            ).strip()

            # 跳过不完整的条目
            if not name or not tag_name or not content:
                continue

            if TAG_NAME_INVALID.search(tag_name):
                logger.warning(
                    f"【手动指令注入】{key} 标签名称包含非法字符 (<, >, 换行)，跳过"
                )
                continue

            if position not in VALID_POSITIONS:
                logger.warning(
                    f"【手动指令注入】{key} 注入位置 '{position}' 无效，"
                    f"回退到 user_message_after"
                )
                position = "user_message_after"

            header = f"<{tag_name}>"
            footer = f"</{tag_name}>"
            cleanup_re = re.compile(
                re.escape(header) + r".*?" + re.escape(footer),
                flags=re.DOTALL,
            )

            # persistent 模式使用带后缀的标签名，与 once 模式彻底隔离
            p_tag_name = f"{tag_name} auto-injected"
            p_header = f"<{p_tag_name}>"
            p_footer = f"</{p_tag_name}>"
            p_cleanup_re = re.compile(
                re.escape(p_header) + r".*?" + re.escape(p_footer),
                flags=re.DOTALL,
            )

            self._entries[name] = {
                "tag_name": tag_name,
                "content": content,
                "position": position,
                "header": header,
                "footer": footer,
                "cleanup_re": cleanup_re,
                "p_tag_name": p_tag_name,
                "p_header": p_header,
                "p_footer": p_footer,
                "p_cleanup_re": p_cleanup_re,
            }

    # -------------------------------------------------------------------
    # 指令处理
    # -------------------------------------------------------------------

    @filter.command("mpinject")
    async def handle_command(
        self, event: AstrMessageEvent, entry_name: str = "", action: str = ""
    ):
        """处理 mpinject 指令。"""
        if not entry_name:
            yield event.plain_result(
                "【手动指令注入】用法:\n"
                "  mpinject <条目名>        持续注入\n"
                "  mpinject <条目名> once   一次性注入\n"
                "  mpinject <条目名> stop   停止注入\n"
                "  mpinject list            列出所有条目"
            )
            return

        # --- mpinject list ---
        if entry_name == "list":
            lines = []
            for name, entry in self._entries.items():
                status = self._active.get(name)
                if status == "persistent":
                    marker = "[persistent]"
                    display_tag = entry["p_tag_name"]
                elif status == "once":
                    marker = "[once]"
                    display_tag = entry["tag_name"]
                else:
                    marker = "[off]"
                    display_tag = entry["tag_name"]
                lines.append(f"  {marker} {name} -> <{display_tag}>")

            if lines:
                yield event.plain_result("【手动指令注入】条目列表:\n" + "\n".join(lines))
            else:
                yield event.plain_result("【手动指令注入】没有已配置的条目。")
            return

        # --- 条目不存在 ---
        if entry_name not in self._entries:
            available = ", ".join(self._entries.keys()) if self._entries else "无"
            yield event.plain_result(
                f"【手动指令注入】未找到条目「{entry_name}」\n可用条目: {available}"
            )
            return

        entry = self._entries[entry_name]

        # --- mpinject <name> stop ---
        if action == "stop":
            if entry_name not in self._active:
                yield event.plain_result(
                    f"【手动指令注入】「{entry_name}」当前未激活。"
                )
                return

            mode = self._active.pop(entry_name)
            if mode == "persistent":
                self._persistent_tags.discard(entry["p_tag_name"])
                self._pending_cleanup.add(entry["p_tag_name"])
            # once 模式的 stop 没有意义（已经注入且不再活跃），
            # 但也不报错，直接从 _active 移除即可

            yield event.plain_result(f"【手动指令注入】「{entry_name}」已停止。")
            return

        # --- mpinject <name> once ---
        if action == "once":
            # 如果之前在 persistent 模式，先清理
            if self._active.get(entry_name) == "persistent":
                self._persistent_tags.discard(entry["p_tag_name"])
                self._pending_cleanup.add(entry["p_tag_name"])

            self._active[entry_name] = "once"
            yield event.plain_result(f"【手动指令注入】「{entry_name}」已激活 (once)。")
            return

        # --- 无效操作 ---
        if action:
            yield event.plain_result(
                f"【手动指令注入】未知操作「{action}」。可用: once, stop"
            )
            return

        # --- mpinject <name>（持续模式）---
        self._active[entry_name] = "persistent"
        self._persistent_tags.add(entry["p_tag_name"])
        self._pending_cleanup.discard(entry["p_tag_name"])
        yield event.plain_result(f"【手动指令注入】「{entry_name}」已激活 (persistent)。")

    # -------------------------------------------------------------------
    # 清理逻辑
    # -------------------------------------------------------------------

    def _clean_string(self, text: str, pattern: re.Pattern) -> str:
        """从字符串中清除匹配的标签内容，并整理多余换行。"""
        cleaned = pattern.sub("", text)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned.strip()

    def _clean_tag_from_request(
        self, req: ProviderRequest, entry: dict[str, Any]
    ) -> int:
        """从 ProviderRequest 的所有位置中清除指定标签。返回清除的片段数。"""
        removed = 0
        pattern = entry["cleanup_re"]
        header = entry["header"]
        footer = entry["footer"]

        # --- 清理 system_prompt ---
        if hasattr(req, "system_prompt") and req.system_prompt:
            if (
                isinstance(req.system_prompt, str)
                and header in req.system_prompt
                and footer in req.system_prompt
            ):
                original = req.system_prompt
                req.system_prompt = self._clean_string(original, pattern)
                if req.system_prompt != original:
                    removed += 1

        # --- 清理 prompt ---
        if hasattr(req, "prompt") and req.prompt:
            if (
                isinstance(req.prompt, str)
                and header in req.prompt
                and footer in req.prompt
            ):
                original = req.prompt
                req.prompt = self._clean_string(original, pattern)
                if req.prompt != original:
                    removed += 1

        # --- 清理 contexts ---
        if hasattr(req, "contexts") and req.contexts:
            filtered = []
            for msg in req.contexts:
                cleaned_msg = self._clean_context_message(
                    msg, header, footer, pattern
                )
                if cleaned_msg is not None:
                    filtered.append(cleaned_msg)
                else:
                    removed += 1
            req.contexts = filtered

        return removed

    def _clean_context_message(
        self,
        msg: Any,
        header: str,
        footer: str,
        pattern: re.Pattern,
    ) -> Any | None:
        """清理单条 context 消息。返回 None 表示整条消息应被移除。"""

        # 格式 1: 纯字符串
        if isinstance(msg, str):
            if header in msg and footer in msg:
                cleaned = self._clean_string(msg, pattern)
                return cleaned if cleaned else None
            return msg

        # 格式 2/3: 字典
        if isinstance(msg, dict):
            content = msg.get("content", "")

            # 字符串内容
            if isinstance(content, str):
                if header in content and footer in content:
                    cleaned = self._clean_string(content, pattern)
                    if not cleaned:
                        return None
                    if cleaned != content:
                        msg_copy = msg.copy()
                        msg_copy["content"] = cleaned
                        return msg_copy
                return msg

            # 列表内容（多模态）
            if isinstance(content, list):
                cleaned_parts = []
                has_changes = False
                for part in content:
                    if (
                        isinstance(part, dict)
                        and part.get("type") == "text"
                        and isinstance(part.get("text"), str)
                    ):
                        text = part["text"]
                        if header in text and footer in text:
                            ct = self._clean_string(text, pattern)
                            if not ct:
                                has_changes = True
                                continue
                            if ct != text:
                                has_changes = True
                                part_copy = part.copy()
                                part_copy["text"] = ct
                                cleaned_parts.append(part_copy)
                                continue
                    cleaned_parts.append(part)

                if not cleaned_parts:
                    return None
                if has_changes:
                    msg_copy = msg.copy()
                    msg_copy["content"] = cleaned_parts
                    return msg_copy
                return msg

        return msg

    # -------------------------------------------------------------------
    # 注入逻辑
    # -------------------------------------------------------------------

    def _inject_text(self, req: ProviderRequest, text: str, position: str):
        """将文本注入到指定位置。"""
        if position == "user_message_before":
            req.prompt = text + "\n\n" + (req.prompt or "")

        elif position == "system_prompt":
            req.system_prompt = (req.system_prompt or "") + "\n\n" + text

        else:  # user_message_after
            prompt = req.prompt or ""
            rag_marker = "<RAG-Faiss-Memory>"
            rag_pos = prompt.find(rag_marker)
            if rag_pos > 0:
                before_rag = prompt[:rag_pos].rstrip()
                from_rag = prompt[rag_pos:]
                req.prompt = before_rag + "\n\n" + text + "\n\n" + from_rag
            else:
                req.prompt = prompt + "\n\n" + text

    def _format_entry(self, entry: dict[str, Any], mode: str = "once") -> str:
        """将条目格式化为 XML 标签包裹的字符串。"""
        if mode == "persistent":
            return f"{entry['p_header']}\n{entry['content']}\n{entry['p_footer']}\n"
        return f"{entry['header']}\n{entry['content']}\n{entry['footer']}\n"

    # -------------------------------------------------------------------
    # 钩子
    # -------------------------------------------------------------------

    @filter.on_llm_request(priority=3)
    async def handle_cleanup(
        self, event: AstrMessageEvent, req: ProviderRequest
    ):
        """清理阶段：清除 persistent 模式和 pending_cleanup 的标签。

        priority=3: 在 FirstWindowInject(2)、PromptTags(1)、LivingMemory(0)
        之前执行，防止我们的标签内容干扰其他插件的清理正则。
        """
        # 收集需要清理的标签名
        tags_to_clean = self._persistent_tags | self._pending_cleanup
        if not tags_to_clean:
            return

        try:
            total_removed = 0
            for entry in self._entries.values():
                if entry["p_tag_name"] in tags_to_clean:
                    p_entry = {
                        "cleanup_re": entry["p_cleanup_re"],
                        "header": entry["p_header"],
                        "footer": entry["p_footer"],
                    }
                    removed = self._clean_tag_from_request(req, p_entry)
                    total_removed += removed

            # pending_cleanup 完成使命，清空
            self._pending_cleanup.clear()

            if total_removed > 0:
                logger.debug(
                    f"【手动指令注入】[清理] 已清理 {total_removed} 处历史注入"
                )

        except Exception as e:
            logger.error(
                f"【手动指令注入】[清理] 发生错误: {e}", exc_info=True
            )

    @filter.on_llm_request(priority=-500)
    async def handle_inject(
        self, event: AstrMessageEvent, req: ProviderRequest
    ):
        """注入阶段：将所有激活条目的内容注入到请求中。

        priority=-500: 与 PromptTags 同级，在 FirstWindowInject(-499)、
        LivingMemory(0) 之后执行，不污染记忆检索。
        """
        if not self._active:
            return

        try:
            # 收集本轮注入完成后需要从 _active 移除的 once 条目
            once_done: list[str] = []

            for name, mode in self._active.items():
                entry = self._entries.get(name)
                if not entry:
                    continue

                formatted = self._format_entry(entry, mode)
                self._inject_text(req, formatted, entry["position"])

                logger.debug(
                    f"【手动指令注入】[注入] {name} ({mode}) "
                    f"-> {entry['position']}"
                )

                if mode == "once":
                    once_done.append(name)

            # 移除已完成的 once 条目
            for name in once_done:
                del self._active[name]

        except Exception as e:
            logger.error(
                f"【手动指令注入】[注入] 发生错误: {e}", exc_info=True
            )

    # -------------------------------------------------------------------
    # 生命周期
    # -------------------------------------------------------------------

    async def terminate(self):
        """插件停止时清理状态。"""
        self._active.clear()
        self._persistent_tags.clear()
        self._pending_cleanup.clear()
        logger.info("【手动指令注入】插件已停止")
