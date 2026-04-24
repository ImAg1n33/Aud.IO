import json
from typing import Any, Mapping

# ==========================================
# 模块 1：核心人设 (Persona)
# ==========================================
SYSTEM_PERSONA = """You are Aud.IO, a helpful voice-first assistant.
Keep responses concise, practical, and friendly."""

# ==========================================
# 模块 2：行为准则与工具约束 (Rules & Constraints)
# ==========================================
TOOL_CONSTRAINTS = """【音乐播放绝对规则】
当用户要求播放音乐，但没有明确指定具体歌名或歌手时（例如“换一首”、“来首同类型的”），你必须作为高级 DJ，自主决定并挑选一首真实存在的具体歌曲。

【动作强制转换警告】（极其重要！）：
当用户说“换一首”、“下一首”、“切歌”时，绝对不允许使用 `skip`、`next_track` 等播放器控制指令！
你必须将其理解为“请为我推荐并播放一首新歌”，并强制生成一首新的具体歌曲填入 `play_keyword`！

【致命格式警告】：
你填入 play_keyword 字段的内容，必须 **仅仅包含真实的『歌手名 歌名』或者『歌名』**！
绝对不允许使用任何代词、形容词、曲风描述或分类占位符！
错误示范 ❌："同类型歌曲"、"换一首"、"周杰伦的歌"、"City Pop"
正确示范 ✅："竹内玛莉亚 Plastic Love"、"中森明菜 Oh No, Oh Yes!"

【上下文利用】：
请务必参考 Context 中提供的 "Currently Playing" 信息。如果用户要求“同类型”，请推断 Currently Playing 属于什么曲风，并据此挑选一首不同的真实歌曲。在 Context 为 none 的情况下，绝对不能说“无法确定风格”，必须自己随机挑选一首进行推荐！"""

# ==========================================
# 模块 3：后台评论家（音乐偏好观察员）
# ==========================================
MEMORY_OBSERVER_SYSTEM_PROMPT = """你是一个“音乐偏好观察员”。

输入：
1) 当前 user_profile.json
2) 刚结束的一轮对话记录（user_input + assistant_reply）

任务：
1) 判断用户是否表达了新的偏好信号（喜欢 / 跳过 / 反感）。
2) 提取用户提到的新标签（例如：下雨天、适合写代码）。
3) 输出 JSON 对象，不要输出任何解释性文字。

输出规则：
1) 如果有明确变化，优先返回 JSON Patch：{"patch": [...]}。
2) 如果没有体现明显偏好变化，返回空对象 {}。
3) patch 只允许修改 /core_taste、/artist_preference、/mood_bias。
"""

# ==========================================
# 模块组装器 (Builder)
# ==========================================
def _context_to_text(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def build_prompt(user_input: str, context: Mapping[str, Any]) -> str:
    # 处理动态上下文
    context_lines = [f"- {key}: {_context_to_text(value)}" for key, value in context.items()]
    context_block = "\n".join(context_lines) if context_lines else "- none"

    # 像搭积木一样，组合出最终的超级 Prompt
    final_prompt = f"""{SYSTEM_PERSONA}

{TOOL_CONSTRAINTS}

Context:
{context_block}

User:
{user_input}
"""
    return final_prompt


def build_memory_observer_messages(
    old_profile: Mapping[str, Any],
    user_input: str,
    assistant_reply: str,
) -> list[dict[str, str]]:
    payload = {
        "user_profile": old_profile,
        "conversation": {
            "user_input": user_input,
            "assistant_reply": assistant_reply,
        },
        "output_schema": {
            "patch": [
                {
                    "op": "add|replace|remove",
                    "path": "/core_taste/... | /artist_preference/... | /mood_bias/...",
                    "value": "optional",
                }
            ]
        },
    }
    return [
        {"role": "system", "content": MEMORY_OBSERVER_SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]