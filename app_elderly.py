# -*- coding: utf-8 -*-
"""老年陪伴助手 适老化网页（Streamlit）。

大字、大按钮、一次一件事；识别情绪 + 自动路由 4 场景 + 两层记忆：
- 会话记忆：本页多轮对话都记得（st.session_state.history）
- 长期档案：按昵称存 memory_<昵称>.json，关掉重开也记得
运行：streamlit run app_elderly.py
"""
import os

import streamlit as st

import elderly_agent

# 部署到云端（Streamlit Cloud 等）时，Key 放在平台的 Secret 里，这里注入成环境变量
# 供 elderly_agent 读取；本地开发没有 Secret，就回退到同目录 .env。
try:
    if "ARK_API_KEY" in st.secrets and not os.environ.get("ARK_API_KEY"):
        os.environ["ARK_API_KEY"] = st.secrets["ARK_API_KEY"]
except Exception:
    pass

st.set_page_config(page_title="老伙伴 · 老年陪伴助手", page_icon="👴", layout="centered")

# 适老化：整体调大字号
st.markdown(
    """
    <style>
    .stApp { font-size: 20px; }
    .stButton button { font-size: 22px; padding: 14px 28px; height: auto; }
    .stTextArea textarea { font-size: 20px; }
    .stTextInput input { font-size: 20px; }
    .big-reply { font-size: 24px; line-height: 1.7; }
    .you { font-size: 20px; color: #666; margin: 6px 0; }
    .me { font-size: 20px; margin: 10px 0; }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("👴 老伙伴 · 老年陪伴助手")
st.caption("有啥事、想说啥，直接跟我说。我能记住您说过的话，陪您聊天、教您用手机、听您讲过去的故事。")

# ---------- 会话状态初始化 ----------
if "nickname" not in st.session_state:
    st.session_state.nickname = ""
if "history" not in st.session_state:
    st.session_state.history = []
if "profile" not in st.session_state:
    st.session_state.profile = ""
if "_loaded_for" not in st.session_state:
    st.session_state._loaded_for = None

# ---------- 身份（昵称） ----------
st.text_input("怎么称呼您？（比如：张大爷）", key="nickname",
              placeholder="告诉我您叫啥，我就能一直记住您啦")

# 昵称变了 → 切换加载对应人的档案
if st.session_state.nickname.strip() != st.session_state._loaded_for:
    st.session_state._loaded_for = st.session_state.nickname.strip()
    mem = elderly_agent.load_memory(st.session_state.nickname)
    st.session_state.profile = (mem or {}).get("profile", "")
    st.session_state.history = list((mem or {}).get("history", []))

font_size = st.slider("字大小（往右拖更大）", 20, 40, 24)

# ---------- 聊天记录 ----------
for m in st.session_state.history:
    if m.get("role") == "user":
        st.markdown(f"<div class='you'>🙋 <b>您：</b>{m['content']}</div>",
                    unsafe_allow_html=True)
    else:
        st.markdown(f"<div class='me'>👴 <b>老伙伴：</b>{m['content']}</div>",
                    unsafe_allow_html=True)

# ---------- 输入 ----------
with st.form("chat_form"):
    text = st.text_area("您想说点什么：", height=120,
                        placeholder="比如：儿子教我发照片，我又忘了，咋弄？")
    submitted = st.form_submit_button("发出去", width="stretch")

if submitted:
    if not text.strip():
        st.warning("您还没说话呢，先在上面写点啥。")
    else:
        with st.spinner("正在想怎么回您…"):
            try:
                result = elderly_agent.respond(
                    text, nickname=st.session_state.nickname,
                    history=st.session_state.history)
            except Exception as e:
                st.error(f"出错了：{e}")
                result = None

        if result:
            st.session_state.history.append({"role": "user", "content": text})
            st.session_state.history.append({"role": "assistant", "content": result["reply"]})
            st.session_state.profile = result["profile"]

            # 小字显示识别到的场景和情绪（给调试/演示看）
            emo_desc = "、".join(f"{e['small']}" for e in result["emotions"]) \
                if result["emotions"] else "无明显情绪"
            st.caption(f"［场景：{result['scenario']}］ ［情绪：{emo_desc}］")

            st.markdown(
                f"<div class='big-reply' style='font-size:{font_size}px;'>"
                f"{result['reply']}</div>",
                unsafe_allow_html=True,
            )

# ---------- 研究数据（可折叠，给课题组看，不打扰老人） ----------
with st.expander("研究数据（课题组用，老人可忽略）"):
    try:
        import research_log
        _s = research_log.stats()
        _sec = _s["mean_ms"] / 1000.0
        st.markdown(
            "**已记录 %d 轮｜参与 %d 人｜追问率 %.1f%%｜平均响应 %.1f 秒**"
            % (_s["n"], _s["n_uid"], _s["followup_rate"], _sec))
        if _s["by_scenario"]:
            st.markdown("**各场景轮次：** " + "、".join(
                "%s %d" % (k, v) for k, v in _s["by_scenario"].items()))
        if _s["by_task_type"]:
            st.markdown("**任务类型：** " + "、".join(
                "%s %d" % (k, v) for k, v in _s["by_task_type"].items()))
        _csv = research_log.export_csv()
        with open(_csv, "rb") as _f:
            st.download_button("下载研究数据（CSV，Excel 可直接打开）",
                               _f.read(),
                               file_name="老伙伴_研究数据.csv",
                               mime="text/csv")
    except Exception as _e:
        st.caption("研究数据读取失败：%s" % _e)

# ---------- 记忆管理（可折叠区，平时不打扰老人） ----------
with st.expander("看看我记住了什么（记忆管理）"):
    if st.session_state.nickname.strip():
        if st.session_state.profile:
            st.markdown("**我记住的（长期档案，关掉重开也在）：**")
            st.markdown(st.session_state.profile)
        else:
            st.info("我还没记住什么，多聊几句我就记住啦。")
        if st.button("清空我的记忆"):
            elderly_agent.clear_memory(st.session_state.nickname)
            st.session_state.profile = ""
            st.session_state.history = []
            st.rerun()
    else:
        st.info("您还没告诉我怎么称呼您，告诉我了才能记住您。")
