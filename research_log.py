# -*- coding: utf-8 -*-
"""研究数据记录模块（research_log）。

作用：把这个网页从「产品」变成「研究工具」。
每轮交互自动落一条记录，供论文与挑战杯报告取数。

为什么必须加：
    现在老人用完了，服务器上什么都不留。没有记录，就无法回答
    「多少轮、多快、追问几次、哪种场景最费劲」——而这些正是报告
    第四、五节要的东西。

记录内容与语料库对齐：
    说话人固定为「老人」、受话人固定为「对机」，与主库 994 条同口径，
    可直接并入或单列一组「网页实测」子集。

存储：
    research_log.jsonl，一行一条，追加写。云端文件系统是临时的，
    所以另提供 export_csv() 与 stats()，界面上加一个导出按钮即可带走。
"""
import os
import json
import time
import hashlib
import difflib

HERE = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(HERE, "research_log.jsonl")

APP_VERSION = "web-1.0"

# 追问的标记词：老人要求重说、否认、换说法时的常见表述
FOLLOWUP_MARKS = [
    "再说一遍", "再说一次", "没听清", "听不清", "你说什么", "你说啥",
    "不对", "不是这个", "不是我要", "听不懂", "没听懂", "不知道你在说",
    "啊？", "啥？", "重新", "换一个",
]

# 任务类型：与报告的「查询类／生成类」二分对齐
TASK_TYPE = {
    "陪伴聊天": "开放类",
    "回忆叙事": "开放类",
    "手机教学": "查询类",
    "生活助手": "查询类",
}


def _uid(nickname):
    """昵称脱敏：不存真名，只存一个稳定的短哈希，便于跨轮归到同一人。"""
    n = (nickname or "").strip()
    if not n:
        return "anon"
    return "U" + hashlib.sha1(n.encode("utf-8")).hexdigest()[:8]


def _norm(s):
    return "".join(str(s or "").split())


def is_followup(text, prev_text):
    """判断这一轮是不是在追问／修正上一轮。

    两条判据，任一成立即算：
      1. 出现追问标记词；
      2. 与上一轮输入的相似度 ≥ 0.60（老人把同一句换了个说法再说）。
    """
    t = _norm(text)
    if not t:
        return False
    for m in FOLLOWUP_MARKS:
        if m in t:
            return True
    p = _norm(prev_text)
    if not p:
        return False
    ratio = difflib.SequenceMatcher(None, p, t).ratio()
    return ratio >= 0.60


def log(*, nickname, text, reply, scenario, emotions, elapsed_ms,
        ok=True, err="", prev_text=""):
    """写一条记录。任何异常都不抛出，绝不影响聊天。"""
    try:
        rec = {
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "app": APP_VERSION,
            "uid": _uid(nickname),
            "speaker": "老人",
            "addressee": "对机",
            "scenario": scenario,
            "task_type": TASK_TYPE.get(scenario, ""),
            "emotions": [e.get("small", "") for e in (emotions or [])],
            "input": text or "",
            "input_len": len(_norm(text)),
            "output": reply or "",
            "output_len": len(_norm(reply)),
            "elapsed_ms": int(elapsed_ms),
            "followup": is_followup(text, prev_text),
            "ok": bool(ok),
            "err": err or "",
        }
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec
    except Exception:
        return None


def load_all():
    """读全部记录；文件不存在或损坏返回空表。"""
    if not os.path.exists(LOG_PATH):
        return []
    out = []
    try:
        with open(LOG_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        return out
    return out


def stats():
    """算出可直接写进报告的几个数。

    返回 dict，含：
      n          总轮次
      n_uid      参与人数
      followup_rate   追问率（%）
      mean_ms    平均响应毫秒
      by_scenario     各场景轮次
      by_task_type    查询类／开放类轮次
    """
    rows = load_all()
    n = len(rows)
    if n == 0:
        return {"n": 0, "n_uid": 0, "followup_rate": 0.0, "mean_ms": 0,
                "by_scenario": {}, "by_task_type": {}}
    n_fu = sum(1 for r in rows if r.get("followup"))
    ms = [r.get("elapsed_ms", 0) for r in rows if r.get("ok")]
    by_s, by_t = {}, {}
    for r in rows:
        k = r.get("scenario", "")
        by_s[k] = by_s.get(k, 0) + 1
        t = r.get("task_type", "")
        if t:
            by_t[t] = by_t.get(t, 0) + 1
    return {
        "n": n,
        "n_uid": len({r.get("uid") for r in rows}),
        "followup_rate": round(n_fu * 100.0 / n, 1),
        "mean_ms": int(sum(ms) / len(ms)) if ms else 0,
        "by_scenario": by_s,
        "by_task_type": by_t,
    }


def export_csv(path=None):
    """导出 CSV（UTF-8 带 BOM，Excel 双击不乱码）。返回文件路径。"""
    import csv
    rows = load_all()
    path = path or os.path.join(HERE, "研究数据导出.csv")
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["时间", "用户编号", "场景", "任务类型", "情绪", "老人原话",
                    "原话字数", "系统回应", "回应字数", "用时毫秒",
                    "是否追问", "是否成功", "错误"])
        for r in rows:
            w.writerow([
                r.get("ts", ""), r.get("uid", ""), r.get("scenario", ""),
                r.get("task_type", ""), "、".join(r.get("emotions", [])),
                r.get("input", ""), r.get("input_len", ""),
                r.get("output", ""), r.get("output_len", ""),
                r.get("elapsed_ms", ""),
                "是" if r.get("followup") else "否",
                "是" if r.get("ok") else "否", r.get("err", ""),
            ])
        # 末尾附一段汇总，方便直接抄进报告
        s = stats()
        w.writerow([])
        w.writerow(["【汇总】"])
        w.writerow(["总轮次", s["n"], "参与人数", s["n_uid"],
                    "追问率(%)", s["followup_rate"], "平均用时(毫秒)", s["mean_ms"]])
        for k, v in s["by_scenario"].items():
            w.writerow(["场景", k, v])
        for k, v in s["by_task_type"].items():
            w.writerow(["任务类型", k, v])
    return path
