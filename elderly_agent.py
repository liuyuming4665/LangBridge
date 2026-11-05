# -*- coding: utf-8 -*-
"""老年陪伴助手核心逻辑：情绪识别 → 话题路由 → LLM 生成 → 记忆。

只用火山方舟一个 Key（deepseek 文本），与云纹小匠同款 API 调用。

记忆分两层：
1. 会话记忆：页面把最近几轮对话（history）传给模型，多轮聊天不断片。
2. 长期档案：按用户昵称存 memory_<昵称>.json，跨会话、关掉重开都记得。
   文件里存 profile（模型逐步提炼出的事实）+ history（最近几轮原文）。
"""
import os
import json
import time
import urllib.request

import emotion_lexicon
import prompts_elderly
import research_log

BASE = "https://ark.cn-beijing.volces.com/api/v3"
TEXT_MODEL = "deepseek-v4-1-flash-260910"
MEMORY_DIR = os.path.dirname(os.path.abspath(__file__))
MAX_HISTORY = 16  # 长期档案里最多存几条消息（约 8 轮对话）


def _load_key():
    # 1) 本地开发：从同目录 .env 读取（最可靠，避免被机器上残留的旧环境变量干扰）。
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("ARK_API_KEY="):
                    v = line.split("=", 1)[1].strip()
                    if v:
                        return v
    # 2) 云端：.env 不存在，用界面层注入的环境变量（来自平台 Secret）。
    k = os.environ.get("ARK_API_KEY")
    if k:
        return k
    raise RuntimeError("未找到 ARK_API_KEY：请填 .env，或在部署平台设置 Secret/环境变量")


def chat(messages, max_tokens=1500, temperature=0.7):
    key = _load_key()
    # thinking=disabled：关掉模型的"思考链"。这个模型默认带思考，会烧大量 token、
    # 变慢、还容易把正式回答截断；关掉后更快（约 4 秒）、更省、回答完整。
    payload = {"model": TEXT_MODEL, "messages": messages,
               "max_tokens": max_tokens, "temperature": temperature,
               "thinking": {"type": "disabled"}}
    req = urllib.request.Request(
        BASE + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + key})
    with urllib.request.urlopen(req, timeout=300) as r:
        res = json.loads(r.read().decode("utf-8"))
    return res["choices"][0]["message"]["content"]


# ---------- 长期记忆（按用户分文件） ----------
def _safe_name(nickname):
    """昵称 → 能当文件名的安全名。"""
    name = (nickname or "").strip() or "访客"
    for ch in '\\/:*?"<>|':
        name = name.replace(ch, "_")
    return name


def memory_path(nickname):
    return os.path.join(MEMORY_DIR, f"memory_{_safe_name(nickname)}.json")


def load_memory(nickname):
    """读取某用户的长期档案，没有则返回 None。"""
    if not (nickname or "").strip():
        return None
    p = memory_path(nickname)
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def save_memory(nickname, data):
    # 记忆是"尽力而为"：写文件失败（如云端文件系统只读/临时盘）绝不影响聊天。
    try:
        with open(memory_path(nickname), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def clear_memory(nickname):
    try:
        p = memory_path(nickname)
        if os.path.exists(p):
            os.remove(p)
    except Exception:
        pass


def _emotion_text(emotions):
    """把情绪识别结果拼成给模型的提示。"""
    if not emotions:
        return "（未识别到明显情绪）"
    parts = []
    for e in emotions:
        parts.append(f"{e['big']}（{emotion_lexicon.BIG_DESC.get(e['big'], '')}）——{e['small']}：{('、'.join(e['words']))}")
    return "；".join(parts)


# ---------- 档案提炼 ----------
_PROFILE_PROMPT = (
    "你是「老伙伴」助手的记忆整理员。下面是你和一位老人的最新对话，还有你已经"
    "记住的档案。请判断：这段对话里有没有值得长期记住的新信息（老人的名字、家人、"
    "身体、习惯、喜好、最近操心的事等）。\n"
    "要求：\n"
    "1. 有就输出新要点，每条一行，以「- 」开头，用大白话、第三人称，"
    "比如「- 老人的儿子在城里工作」；\n"
    "2. 不要重复档案里已有的信息；\n"
    "3. 没有任何新信息，就只输出两个字：无。\n\n"
    "【已有档案】\n{profile}\n\n【最新对话】\n{dialogue}"
)


def _update_profile(profile, dialogue):
    """从最新一轮对话里提炼新事实，追加进档案。失败时保留原档案。"""
    try:
        out = chat(
            [{"role": "user", "content": _PROFILE_PROMPT.format(
                profile=profile or "（还没有）", dialogue=dialogue)}],
            max_tokens=400, temperature=0.2,
        ).strip()
    except Exception:
        return profile
    if not out or out in ("无", "无。", "没有", "（无）"):
        return profile
    return (profile.rstrip() + "\n" + out) if profile else out


def respond(text, nickname=None, history=None):
    """输入老人说的话 → 返回 {scenario, emotions, reply, profile}。

    nickname：非空则读写该用户的长期档案（跨会话记忆）。
    history：本页会话的对话记录（[{role, content}, ...]），用于多轮上下文。
    """
    text = (text or "").strip()
    _t0 = time.time()
    prev_text = ""
    if history:
        for _m in reversed(history):
            if _m.get("role") == "user":
                prev_text = _m.get("content", "")
                break
    scenario = emotion_lexicon.route(text)
    emotions = emotion_lexicon.detect_emotion(text)

    # 读取长期档案
    mem = load_memory(nickname) if nickname else None
    profile = (mem or {}).get("profile", "")
    past = (mem or {}).get("history", [])

    # 上下文：优先本页会话最近几轮；没有（刚打开）就用上次存档的最近几轮
    recent = (history or [])[-16:]
    context = recent if recent else past

    system = prompts_elderly.SCENARIOS[scenario]
    if profile:
        system += "\n\n【你记得这位老人的情况，聊天时自然地用上，别生硬复述】\n" + profile

    msgs = [{"role": "system", "content": system}]
    for m in context:
        role = m.get("role")
        content = m.get("content", "")
        if role in ("user", "assistant") and content:
            msgs.append({"role": role, "content": content})
    msgs.append({"role": "user", "content":
                 f"老人说的话：{text}\n识别到的情绪：{_emotion_text(emotions)}\n请用大白话、亲切耐心地回应。"})

    # 生成回应；失败也要记一条——否则“失败”这件事就消失了
    try:
        reply = chat(msgs)
    except Exception as e:
        research_log.log(nickname=nickname, text=text, reply="",
                         scenario=scenario, emotions=emotions,
                         elapsed_ms=(time.time() - _t0) * 1000,
                         ok=False, err=str(e)[:200], prev_text=prev_text)
        raise

    # 更新并保存长期档案
    new_profile = profile
    if nickname:
        new_profile = _update_profile(profile, f"老人：{text}\n助手：{reply}")
        new_hist = (past + [{"role": "user", "content": text},
                            {"role": "assistant", "content": reply}])[-MAX_HISTORY:]
        save_memory(nickname, {
            "nickname": nickname,
            "profile": new_profile,
            "history": new_hist,
            "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
        })

    _ms = (time.time() - _t0) * 1000
    research_log.log(nickname=nickname, text=text, reply=reply,
                     scenario=scenario, emotions=emotions,
                     elapsed_ms=_ms, prev_text=prev_text)

    return {"scenario": scenario, "emotions": emotions,
            "reply": reply, "profile": new_profile, "elapsed_ms": int(_ms)}
