"""血战甄嬛传 —— 游戏引擎：夜晚向导、白天行动、提名投票、胜负判定。

时间模型：夜 n → 天 n → 夜 n+1 …
状态到期点 (e, n)：e="dawn"（夜 n 结束时清除）或 e="dusk"（天 n 结束时清除）。
- 安陵容毒（夜 n 下）：至 dusk n（覆盖当夜+次日白天）
- 敦亲王灌酒（天 n）：至 dawn n+1
- 孙答应连锁全村醉：至 dawn n+1
- 守护（夜 n）：至 dawn n
- 一丈红（夜 n 赐）：至 dusk n（次日白天生效）
- 禁言（天 n）：至 dusk n
"""

import math
import random
from copy import deepcopy

from .roles import ROLE_BY_ID

DEMON_KINDS_HUANGSHANG_LINE = {"huangshang", "nvhuang", "basic"}


class NeedDecision(Exception):
    """结算过程中需要说书人裁定。"""

    def __init__(self, key: str, prompt: str, options: list):
        super().__init__(prompt)
        self.key = key
        self.prompt = prompt
        self.options = options  # [{value, label}]


# ---------------- 基础工具 ----------------

def label(p) -> str:
    return f"{p['seat']}号 {p['name']}（{ROLE_BY_ID[p['role']]['name']}）"


def pub_label(p) -> str:
    return f"{p['seat']}号 {p['name']}"


def get_p(game, seat):
    return game["seats"][seat - 1]


def alive_players(game):
    return [p for p in game["seats"] if p["alive"]]


def alive_count(game):
    return len(alive_players(game))


def vote_threshold(game):
    return math.ceil(alive_count(game) / 2)


def living_neighbors(game, seat):
    """返回 (左邻, 右邻) 存活玩家（座位环）。"""
    n = len(game["seats"])
    idx = seat - 1
    left = right = None
    for d in range(1, n):
        cand = game["seats"][(idx - d) % n]
        if cand["alive"]:
            left = cand
            break
    for d in range(1, n):
        cand = game["seats"][(idx + d) % n]
        if cand["alive"]:
            right = cand
            break
    return left, right


def circ_dist(n, a, b):
    d = abs(a - b)
    return min(d, n - d)


def add_status(p, stype, source, e, n):
    p["statuses"].append({"type": stype, "source": source, "e": e, "n": n})


def prune_statuses(game, e, n):
    for p in game["seats"]:
        p["statuses"] = [s for s in p["statuses"]
                         if not (s["e"] == e and s["n"] <= n)]


def has_status(p, stype):
    return any(s["type"] == stype for s in p["statuses"])


def huanbi_adjacent_drunk(game, p):
    if p["role"] != "huanbi":
        return False
    n = len(game["seats"])
    for q in game["seats"]:
        if q["role"] == "guojunwang" and circ_dist(n, p["seat"] - 1, q["seat"] - 1) == 1:
            return True
    return False


def is_impaired(game, p):
    """醉酒或中毒（技能失效，信息可为假）。"""
    return (has_status(p, "drunk") or has_status(p, "poisoned")
            or huanbi_adjacent_drunk(game, p))


def impair_tag(game, p):
    return "【醉/毒：技能失效，信息应给假】" if is_impaired(game, p) else ""


def log(game, msg):
    game["log"].append(msg)


def role_in_play(game, rid):
    for p in game["seats"]:
        if p["role"] == rid:
            return p
    return None


# ---------------- 开局 ----------------

def start_game(game):
    if game.get("phase") not in (None, "prepare"):
        raise ValueError("对局已开始")
    roles = [s["role"] for s in game["seats"]]
    if not all(roles):
        raise ValueError("尚有座位未分配角色")

    for p in game["seats"]:
        team = ROLE_BY_ID[p["role"]]["team"]
        p.update({
            "original_role": p["role"],
            "alive": True,
            "alignment": "evil" if team in ("minion", "demon") else "good",
            "ghost_vote": True,
            "is_demon": team == "demon",
            "demon_kind": p["role"] if team == "demon" else None,
            "is_favored": False,
            "favored_nights": 0,
            "statuses": [],
            "flags": {},
        })

    game.update({
        "phase": "night",
        "night_number": 1,
        "day_number": 0,
        "winner": None,
        "win_reason": None,
        "log": [],
        "pending_night_events": [],
        "last_private_chats": [],
        "huanghou_disabled": False,
        "baby": None,
    })

    # 对食：苏培盛 + 槿汐 → 槿汐为爪牙
    if role_in_play(game, "supeisheng") and role_in_play(game, "jinxi"):
        jx = role_in_play(game, "jinxi")
        jx["alignment"] = "evil"
        jx["flags"]["duishi"] = True
        log(game, f"对食：{label(jx)} 开局即为爪牙（邪恶阵营）")

    log(game, "游戏开始，进入首夜")
    build_night_steps(game)
    return game


# ---------------- 夜晚步骤构建 ----------------

def _opt(p, note=""):
    return {"seat": p["seat"], "label": pub_label(p), "note": note}


def _step(sid, kind, actor, title, prompt, options, count=1, optional=False, extras=None):
    return {
        "id": sid, "kind": kind,
        "seat": actor["seat"] if actor else None,
        "actor": label(actor) if actor else None,
        "title": title, "prompt": prompt,
        "pick": {"count": count, "options": options},
        "optional": optional,
        "extras": extras or {},
        "collected": None,
    }


def build_night_steps(game):
    n = game["night_number"]
    first = n == 1
    steps = []
    alive = alive_players(game)

    def find(rid):
        for p in alive:
            if p["role"] == rid and not p.get("is_favored"):
                return p
        return None

    if first:
        evil = [p for p in game["seats"] if p["alignment"] == "evil"]
        info = "、".join(label(p) for p in evil)
        steps.append(_step("evil_info", "info", None, "邪恶阵营互认",
                           f"唤醒邪恶阵营互认：{info}", [], count=0))

    alr = find("anlingrong")
    if alr:
        females = [p for p in alive if ROLE_BY_ID[p["role"]]["gender"] == "F" and p is not alr]
        steps.append(_step("alr_info", "pick", alr, "安陵容 · 情报",
                           "说书人选择一名女性角色告知安陵容（真实信息）",
                           [_opt(p, ROLE_BY_ID[p["role"]]["name"]) for p in females]))
        steps.append(_step("alr_poison", "pick", alr, "安陵容 · 香粉下毒",
                           "选择一名女性角色下毒（至下一个黄昏），或无行动",
                           [_opt(p) for p in females], optional=True))

    gjw = find("guojunwang")
    if gjw and not first:
        lastp = gjw["flags"].get("last_protect")
        steps.append(_step("protect", "pick", gjw, "果郡王 · 守护",
                           "守护一名玩家（免刀免睡），可守自己，不可连守同一人",
                           [_opt(p, "上晚已守，不可连守" if p["seat"] == lastp else "")
                            for p in alive], optional=True))

    fav = next((p for p in alive if p["is_favored"]), None)
    if fav:
        steps.append(_step("jinzu", "pick", fav, "宠妃 · 禁足",
                           "使一名玩家当晚无法行动（不可连续同一人），或无行动",
                           [_opt(p, "上晚已禁足" if p["seat"] == fav["flags"].get("last_jinzu") else "")
                            for p in alive if p is not fav], optional=True))

    demon = next((p for p in alive if p["is_demon"]), None)
    if demon:
        kind = demon["demon_kind"]
        others = [p for p in alive if p is not demon]
        if kind == "huangshang":
            if not demon["flags"].get("yulu_used"):
                steps.append(_step("yulu", "pick", demon, "皇上 · 雨露均沾（成功仅一次）",
                                   "选择一名玩家侍寝：女性角色将转化为宠妃；"
                                   "失败不消耗机会（不反馈失败原因），或无行动",
                                   [_opt(p) for p in others], optional=True))
            if game.get("baby") and not game["baby"]["placed"]:
                steps.append(_step("baby", "pick", demon, "皇上 · 放置宝宝",
                                   "将宝宝放置于一名玩家身上（类型由说书人决定），或暂不放置",
                                   [_opt(p) for p in alive], optional=True))
            if not first:
                steps.append(_step("kill", "pick", demon, "皇上 · 君要臣死",
                                   "选择一名玩家杀害",
                                   [_opt(p) for p in others]))
        elif kind in ("nvhuang", "basic") and not first:
            name = "女皇" if kind == "nvhuang" else "皇后（继任恶魔）"
            steps.append(_step("kill", "pick", demon, f"{name} · 夜杀",
                               "选择一名玩家杀害", [_opt(p) for p in others]))
        elif kind == "taihou" and not first:
            steps.append(_step("kill", "pick", demon, "太后 · 宁枉勿纵",
                               "选择两名玩家，几人死亡由说书人结算时裁定",
                               [_opt(p) for p in others], count=2))
        elif kind == "taishanghuang" and not first:
            steps.append(_step("kill", "pick", demon, "回魂太上皇 · 心狠手辣",
                               "选择一名玩家杀害（可选择自己：自刀传位）",
                               [_opt(p, "自刀传位" if p is demon else "") for p in alive]))

    yly = find("yelanyi")
    if yly and not yly["flags"].get("revive_used"):
        dead = [p for p in game["seats"] if not p["alive"]]
        if dead:
            steps.append(_step("revive", "pick", yly, "叶澜依 · 你的福气在后头（全局一次）",
                               "祝福一名死亡玩家，白天复活（技能重置），或无行动",
                               [_opt(p) for p in dead], optional=True))

    if first:
        yr = find("yurao")
        if yr:
            goods = [p for p in alive if p["alignment"] == "good" and p is not yr]
            steps.append(_step("bind", "pick", yr, "玉娆 · 姐妹影分身",
                               "说书人选择一名善良玩家告知玉娆（其被恶魔夜杀时玉娆陪葬）",
                               [_opt(p, ROLE_BY_ID[p["role"]]["name"]) for p in goods]))

    jx = find("jinxi")
    if jx:
        steps.append(_step("jinxi", "pick", jx, "槿汐姑姑 · 打听小道消息",
                           "选择两名玩家，得知其中是否有恶魔",
                           [_opt(p) for p in alive if p is not jx], count=2))

    wsc = find("wenshichu")
    if wsc:
        steps.append(_step("check", "pick", wsc, "温实初 · 太医",
                           "查验一名玩家是否中毒/醉酒（若是，默认为其解除）",
                           [_opt(p) for p in alive if p is not wsc],
                           extras={"cure_toggle": True}))

    sag = find("sanage")
    if sag:
        left, right = living_neighbors(game, sag["seat"])
        opts = []
        if left:
            opts.append(_opt(left, "左邻"))
        if right and right is not left:
            opts.append(_opt(right, "右邻"))
        steps.append(_step("sanage", "pick", sag, "三阿哥 · 染指",
                           "染指左右两边其中一名存活玩家，或无行动（结算给成功/失败手势）",
                           opts, optional=True))

    hf = find("huafei")
    if hf:
        note = "仅剩3人，技能失效" if alive_count(game) <= 3 else ""
        steps.append(_step("huafei", "pick", hf, "华妃 · 一丈红",
                           f"赐一名玩家一丈红（次日其发起提名即暴毙），或无行动 {note}",
                           [_opt(p) for p in alive if p is not hf], optional=True))

    game["night_state"] = {"number": n, "steps": steps}


def record_action(game, step_id, collected):
    ns = game.get("night_state")
    if not ns:
        raise ValueError("当前不在夜晚")
    for s in ns["steps"]:
        if s["id"] == step_id:
            if collected is not None:
                cnt = s["pick"]["count"]
                if not collected.get("no_action") and cnt > 0:
                    ts = collected.get("targets", [])
                    if len(ts) != cnt:
                        raise ValueError(f"需选择 {cnt} 名目标")
            s["collected"] = collected
            return game
    raise ValueError("未知步骤")


# ---------------- 夜晚结算 ----------------

def _need(answers, key, prompt, options):
    if key in answers:
        return answers[key]
    raise NeedDecision(key, prompt, options)


def _collected(g, kind):
    for s in g["night_state"]["steps"]:
        if s["id"] == kind and s["collected"] and not s["collected"].get("no_action"):
            return s
    return None


def resolve_night(game, answers, commit=False):
    """在副本上结算。返回 (result_dict, new_game_or_None)。"""
    g = deepcopy(game)
    rpt = {"deaths": [], "revived": [], "packets": [], "notes": [], "winner": None}
    try:
        _run_night(g, answers or {}, rpt)
    except NeedDecision as d:
        return {"pending": {"key": d.key, "prompt": d.prompt, "options": d.options}}, None
    rpt["winner"] = g.get("winner")
    rpt["win_reason"] = g.get("win_reason")
    if commit:
        _commit_night(g)
        return {"report": rpt, "committed": True}, g
    return {"report": rpt, "committed": False}, None


def _kill(g, rpt, seat, cause, killer=None):
    """死亡结算与连锁。cause: demon_kill/execution/chain/ability/counter/baby"""
    p = get_p(g, seat)
    if not p["alive"]:
        return
    p["alive"] = False
    rpt["deaths"].append({"seat": seat, "name": p["name"], "role": p["role"], "cause": cause})
    log(g, f"死亡：{label(p)}（{cause}）")

    # 狂徒 → 孙答应
    if p["role"] == "kuangtu":
        sd = role_in_play(g, "sundaying")
        if sd and sd["alive"] and not sd.get("is_favored"):
            if g["phase"] == "night":
                rpt["notes"].append("狂徒死亡 → 孙答应当晚随之死亡")
                _kill(g, rpt, sd["seat"], "chain")
            else:
                g["pending_night_events"].append({"type": "sundaying_death"})
                log(g, "狂徒死亡：孙答应将于下个夜晚死亡")

    # 孙答应 → 全村民醉酒一天一夜（宠妃化后失去原技能）
    if p["role"] == "sundaying" and not p.get("is_favored"):
        n = g["night_number"]
        cnt = 0
        for q in g["seats"]:
            if q["alive"] and ROLE_BY_ID[q["role"]]["team"] == "townsfolk":
                add_status(q, "drunk", "sundaying", "dawn", n + 1)
                cnt += 1
        rpt["notes"].append(f"孙答应死亡：{cnt} 名村民醉酒一天一夜")
        log(g, f"孙答应死亡：全体村民（{cnt}人）醉酒一天一夜")

    # 玉娆羁绊（仅恶魔夜杀）
    if cause == "demon_kill":
        for q in g["seats"]:
            if (q["alive"] and q["role"] == "yurao" and not q.get("is_favored")
                    and q["flags"].get("bond") == seat):
                if is_impaired(g, q):
                    rpt["notes"].append(f"玉娆醉/毒：绑定的 {pub_label(p)} 被杀但玉娆不陪葬")
                else:
                    rpt["notes"].append(f"玉娆绑定的 {pub_label(p)} 被恶魔杀死 → 玉娆陪葬")
                    _kill(g, rpt, q["seat"], "chain")

    # 敬妃：被皇上系恶魔杀死 → 得知邪恶存活数
    if (p["role"] == "jingfei" and not p.get("is_favored") and cause == "demon_kill"
            and killer and killer.get("demon_kind") in DEMON_KINDS_HUANGSHANG_LINE):
        evil_alive = sum(1 for q in g["seats"] if q["alive"] and q["alignment"] == "evil")
        rpt["packets"].append({
            "seat": p["seat"], "role": "jingfei", "kind": "jingfei_death",
            "lines": [f"敬妃死前得知：场上还有 {evil_alive} 名邪恶玩家存活"],
            "malfunction": is_impaired(g, p)})

    # 年羹尧：死亡当晚带走一人
    if p["role"] == "niangengyao" and not p["flags"].get("takealong_used"):
        p["flags"]["takealong_used"] = True
        if is_impaired(g, p):
            rpt["notes"].append("年羹尧死亡时醉/毒：带人失败（技能已消耗）")
        elif g["phase"] == "night":
            opts = [{"value": q["seat"], "label": pub_label(q)}
                    for q in g["seats"] if q["alive"]]
            if opts:
                tgt = _need(g["_answers"], f"nian_{seat}",
                            f"年羹尧（{pub_label(p)}）死亡，选择带走一名玩家", opts)
                rpt["notes"].append(f"年羹尧带走 {pub_label(get_p(g, int(tgt)))}")
                _kill(g, rpt, int(tgt), "ability")
        else:
            g["pending_night_events"].append({"type": "nian_takealong", "seat": seat})
            log(g, "年羹尧白天死亡：当晚可带走一名玩家")

    # 恶魔死亡 → 皇后继任
    if p["is_demon"]:
        p["is_demon"] = False
        _succession(g, rpt, p)


def _succession(g, rpt, dead_demon):
    if g.get("_suppress_succession"):
        return
    hh = role_in_play(g, "huanghou")
    if (hh and hh["alive"] and not hh["is_demon"] and not g["huanghou_disabled"]
            and alive_count(g) >= 5):
        hh["is_demon"] = True
        hh["alignment"] = "evil"
        if dead_demon["demon_kind"] == "taihou":
            hh["demon_kind"] = "taihou"
            note = f"皇后（{pub_label(hh)}）秘密继任为新太后（每晚双选，说书人裁定死亡数）"
        else:
            hh["demon_kind"] = "basic"
            note = f"皇后（{pub_label(hh)}）秘密继任为新恶魔（仅基础夜杀）"
        rpt["notes"].append(note)
        log(g, note)


def check_win(g, rpt=None):
    if g.get("winner"):
        return
    demons = [p for p in g["seats"] if p["alive"] and p["is_demon"]]
    goods = [p for p in g["seats"] if p["alive"] and p["alignment"] == "good"]
    reason = None
    if not demons:
        g["winner"], reason = "good", "恶魔已死且无人继任，善良阵营获胜"
    elif alive_count(g) <= 2:
        g["winner"], reason = "evil", "场上仅剩两名存活玩家且恶魔在场，邪恶阵营获胜"
    elif not goods:
        g["winner"], reason = "evil", "善良玩家全部死亡，邪恶阵营获胜"
    if g.get("winner"):
        g["win_reason"] = reason
        g["phase"] = "ended"
        log(g, f"游戏结束：{reason}")
        if rpt is not None:
            rpt["notes"].append(reason)


def _run_night(g, answers, rpt):
    g["_answers"] = answers
    n = g["night_number"]
    ns = g["night_state"]
    voided = set()

    def collected(kind):
        s = next((x for x in ns["steps"] if x["id"] == kind), None)
        if not s or not s["collected"] or s["collected"].get("no_action"):
            return None
        if s["seat"] in voided:
            rpt["notes"].append(f"{s['title']}：行动者被禁足，行动无效")
            return None
        return s

    def actor(s):
        return get_p(g, s["seat"])

    def targets(s):
        return [int(t) for t in s["collected"]["targets"]]

    # 0. 上白天遗留的夜晚事件
    for ev in g["pending_night_events"]:
        if ev["type"] == "sundaying_death":
            sd = role_in_play(g, "sundaying")
            if sd and sd["alive"]:
                rpt["notes"].append("狂徒已死：孙答应今夜死亡")
                _kill(g, rpt, sd["seat"], "chain")
        elif ev["type"] == "qiguiren_wrong":
            qg = role_in_play(g, "qiguiren")
            if qg and qg["alive"]:
                rpt["notes"].append("祺贵人白天检举猜错：今夜暴毙")
                _kill(g, rpt, qg["seat"], "ability")
        elif ev["type"] == "qiguiren_correct":
            opts = [{"value": q["seat"], "label": pub_label(q)} for q in alive_players(g)]
            tgt = _need(answers, "qiguiren_victim",
                        "祺贵人检举猜对：说书人选择今夜随机死亡的一名玩家", opts)
            rpt["notes"].append(f"祺贵人检举猜对：{pub_label(get_p(g, int(tgt)))} 今夜死亡")
            _kill(g, rpt, int(tgt), "ability")
        elif ev["type"] == "nian_takealong":
            p = get_p(g, ev["seat"])
            opts = [{"value": q["seat"], "label": pub_label(q)} for q in alive_players(g)]
            if opts:
                tgt = _need(answers, f"nian_{ev['seat']}",
                            f"年羹尧（{pub_label(p)}）白天死亡，今夜带走一名玩家", opts)
                rpt["notes"].append(f"年羹尧带走 {pub_label(get_p(g, int(tgt)))}")
                _kill(g, rpt, int(tgt), "ability")

    # 1. 宠妃禁足（先行，决定 voided）
    s = collected("jinzu")
    if s:
        fav = actor(s)
        t = targets(s)[0]
        if is_impaired(g, fav):
            rpt["notes"].append(f"宠妃醉/毒：禁足无效 {impair_tag(g, fav)}")
        else:
            voided.add(t)
            fav["flags"]["last_jinzu"] = t
            rpt["notes"].append(f"宠妃禁足 {pub_label(get_p(g, t))}：其今晚行动无效")

    # 2. 安陵容
    s = collected("alr_info")
    if s:
        alr = actor(s)
        t = get_p(g, targets(s)[0])
        rpt["packets"].append({
            "seat": alr["seat"], "role": "anlingrong", "kind": "alr_info",
            "lines": [f"告知安陵容：{pub_label(t)} 是 {ROLE_BY_ID[t['role']]['name']}"],
            "malfunction": False})
    s = collected("alr_poison")
    if s:
        alr = actor(s)
        t = targets(s)[0]
        if is_impaired(g, alr):
            rpt["notes"].append(f"安陵容醉/毒：下毒无效 {impair_tag(g, alr)}")
        else:
            add_status(get_p(g, t), "poisoned", "anlingrong", "dusk", n)
            rpt["notes"].append(f"安陵容对 {pub_label(get_p(g, t))} 下毒（至下个黄昏）")

    # 3. 果郡王守护
    protected = set()
    s = collected("protect")
    if s:
        gjw = actor(s)
        t = targets(s)[0]
        if is_impaired(g, gjw):
            rpt["notes"].append(f"果郡王醉/毒：守护无效 {impair_tag(g, gjw)}")
        elif t == gjw["flags"].get("last_protect"):
            rpt["notes"].append("果郡王连守同一人：守护无效（规则禁止）")
        else:
            protected.add(t)
            gjw["flags"]["pending_last_protect"] = t
            rpt["notes"].append(f"果郡王守护 {pub_label(get_p(g, t))}（免刀免睡）")

    # 4. 雨露均沾
    yulu_target = None
    s = collected("yulu")
    if s:
        hs = actor(s)
        t = targets(s)[0]
        tp = get_p(g, t)
        fail = None
        if is_impaired(g, hs):
            fail = f"皇上醉/毒：侍寝失败 {impair_tag(g, hs)}"
        elif ROLE_BY_ID[tp["role"]]["gender"] != "F":
            fail = f"目标 {pub_label(tp)} 为男性角色：侍寝失败"
        elif t in protected:
            fail = f"{pub_label(tp)} 被果郡王守护（免睡）：侍寝失败"
        if fail:
            rpt["notes"].append(f"{fail}（机会保留，对皇上不反馈失败原因）")
            rpt["packets"].append({"seat": hs["seat"], "role": "huangshang",
                                   "kind": "yulu", "ctx": {"target": t},
                                   "lines": ["侍寝失败：雨露均沾机会保留"],
                                   "malfunction": False})
        else:
            hs["flags"]["yulu_used"] = True
            yulu_target = t
            tp["is_favored"] = True
            tp["alignment"] = "evil"
            tp["favored_nights"] = 0
            rpt["notes"].append(f"雨露均沾：{label(tp)} 转化为宠妃（邪恶阵营，失去原技能，获得禁足）")
            rpt["packets"].append({"seat": hs["seat"], "role": "huangshang",
                                   "kind": "yulu", "ctx": {"target": t},
                                   "lines": [f"侍寝成功：{pub_label(tp)} 已转化为宠妃"],
                                   "malfunction": False})
            log(g, f"雨露均沾：{pub_label(tp)} 成为宠妃")

    # 5. 宝宝放置
    s = collected("baby")
    if s and g.get("baby") and not g["baby"]["placed"]:
        t = targets(s)[0]
        btype = g["baby"]["type"] or _need(
            answers, "baby_type", "说书人决定宝宝类型",
            [{"value": "bomb", "label": "炸弹宝宝（炸死被放置的玩家）"},
             {"value": "cat", "label": "狸猫宝宝（炸死宠妃）"}])
        g["baby"]["type"] = btype
        g["baby"]["placed"] = True
        if btype == "bomb":
            rpt["notes"].append(f"炸弹宝宝：{pub_label(get_p(g, t))} 当夜被炸死")
            _kill(g, rpt, t, "baby")
        else:
            fav = next((q for q in g["seats"] if q["is_favored"] and q["alive"]), None)
            if fav:
                rpt["notes"].append(f"狸猫宝宝：宠妃 {pub_label(fav)} 被炸死（狸猫换太子）")
                _kill(g, rpt, fav["seat"], "baby")

    # 6. 恶魔杀
    demon_kill_target = None
    s = collected("kill")
    if s:
        d = actor(s)
        kind = d["demon_kind"]
        if is_impaired(g, d):
            rpt["notes"].append(f"恶魔醉/毒：今夜杀害无效 {impair_tag(g, d)}")
        elif kind == "taihou":
            t1, t2 = targets(s)
            allowed = [t for t in (t1, t2) if t not in protected]
            for t in (t1, t2):
                if t in protected:
                    rpt["notes"].append(f"{pub_label(get_p(g, t))} 被守护，不会死亡")
            opts = [{"value": "none", "label": "无人死亡"}]
            opts += [{"value": str(t), "label": f"仅 {pub_label(get_p(g, t))} 死亡"} for t in allowed]
            if len(allowed) == 2:
                opts.append({"value": "both", "label": "两人都死"})
            pick = _need(answers, "taihou_deaths",
                         f"太后选择了 {pub_label(get_p(g, t1))} 与 {pub_label(get_p(g, t2))}，几人死亡？", opts)
            chosen = [] if pick == "none" else allowed if pick == "both" else [int(pick)]
            for t in chosen:
                _kill(g, rpt, t, "demon_kill", killer=d)
        else:
            t = targets(s)[0]
            tp = get_p(g, t)
            if kind == "taishanghuang" and t == d["seat"]:
                chats = g.get("last_private_chats", [])
                opts = [{"value": c, "label": pub_label(get_p(g, c))}
                        for c in chats if get_p(g, c)["alive"]]
                if opts:
                    succ = int(_need(answers, "tsh_successor",
                                     "太上皇自刀传位：从上个白天私聊过说书人的玩家中选择新皇上", opts))
                    sp = get_p(g, succ)
                    g["_suppress_succession"] = True
                    _kill(g, rpt, d["seat"], "demon_kill", killer=d)
                    g["_suppress_succession"] = False
                    sp["is_demon"] = True
                    sp["demon_kind"] = "huangshang"
                    sp["alignment"] = "evil"
                    sp["role"] = "huangshang"
                    rpt["notes"].append(f"太上皇自刀传位：{pub_label(sp)} 成为新皇上（完整皇上技能）")
                    log(g, f"太上皇传位：{pub_label(sp)} 成为新皇上")
                else:
                    rpt["notes"].append("太上皇自刀但上个白天无人私聊说书人：传位失败")
                    _kill(g, rpt, d["seat"], "demon_kill", killer=d)
            elif t in protected:
                rpt["notes"].append(f"{pub_label(tp)} 被果郡王守护：今夜无人死于恶魔之手")
            elif (tp["role"] == "zhenhuan" and tp["alive"] and not tp.get("is_favored")
                  and kind in ("huangshang", "basic") and not is_impaired(g, tp)):
                g["huanghou_disabled"] = True
                g["_suppress_succession"] = True
                _kill(g, rpt, d["seat"], "counter", killer=tp)
                g["_suppress_succession"] = False
                tp["is_demon"] = True
                tp["demon_kind"] = "nvhuang"
                tp["alignment"] = "evil"
                tp["role"] = "nvhuang"
                note = f"甄嬛反杀！{pub_label(tp)} 成为女皇（邪恶新领袖，每晚一刀）；皇后技能永久失效"
                rpt["notes"].append(note)
                log(g, note)
            else:
                if tp["role"] == "zhenhuan" and is_impaired(g, tp):
                    rpt["notes"].append("甄嬛醉/毒：反杀失效，正常死亡")
                demon_kill_target = t
                _kill(g, rpt, t, "demon_kill", killer=d)

    # 7. 三阿哥染指
    s = collected("sanage")
    if s:
        sag = actor(s)
        if sag["alive"]:
            t = targets(s)[0]
            tp = get_p(g, t)
            r = ROLE_BY_ID[tp["role"]]
            if is_impaired(g, sag):
                rpt["packets"].append({"seat": sag["seat"], "role": "sanage",
                                       "kind": "sanage", "ctx": {"target": t},
                                       "lines": [f"三阿哥醉/毒：染指无效果，手势任意 {impair_tag(g, sag)}"],
                                       "malfunction": True})
            elif tp["is_demon"] and tp["demon_kind"] == "nvhuang":
                rpt["notes"].append("三阿哥染指女皇：大逆不道，当即处死！")
                _kill(g, rpt, sag["seat"], "ability")
            elif t == yulu_target or t == demon_kill_target:
                rpt["notes"].append("三阿哥与皇上同晚沾染同一目标：被捉奸，被皇上杀死")
                _kill(g, rpt, sag["seat"], "ability")
            elif tp["is_favored"]:
                tp["is_favored"] = False
                orig_team = ROLE_BY_ID[tp["original_role"]]["team"]
                tp["alignment"] = "evil" if orig_team in ("minion", "demon") else "good"
                tp["favored_nights"] = 0
                rpt["notes"].append(f"三阿哥染指宠妃 {pub_label(tp)}：恢复原阵营与原技能（给成功手势）")
                rpt["packets"].append({"seat": sag["seat"], "role": "sanage",
                                       "kind": "sanage", "ctx": {"target": t},
                                       "lines": ["手势：成功"], "malfunction": False})
                log(g, f"{pub_label(tp)} 被三阿哥染指，脱离宠妃身份")
            elif (r["gender"] == "M" or tp["role"] in ("taihou", "qifei", "longyue")
                  or (r["team"] == "minion" and r["gender"] == "F")):
                rpt["packets"].append({"seat": sag["seat"], "role": "sanage",
                                       "kind": "sanage", "ctx": {"target": t},
                                       "lines": [f"手势：失败（目标 {pub_label(tp)} 不可染指）"],
                                       "malfunction": False})
            else:
                rpt["packets"].append({"seat": sag["seat"], "role": "sanage",
                                       "kind": "sanage", "ctx": {"target": t},
                                       "lines": [f"手势：成功（目标 {pub_label(tp)}，无额外效果）"],
                                       "malfunction": False})

    # 8. 叶澜依复活
    s = collected("revive")
    if s:
        yly = actor(s)
        if yly["alive"]:
            t = targets(s)[0]
            tp = get_p(g, t)
            yly["flags"]["revive_used"] = True
            if is_impaired(g, yly):
                rpt["notes"].append("叶澜依醉/毒：复活失败（机会已消耗）")
            elif tp["alive"]:
                rpt["notes"].append(f"{pub_label(tp)} 已存活，复活无效")
            else:
                tp["alive"] = True
                tp["ghost_vote"] = True
                tp["flags"] = {}
                tp["statuses"] = []
                rpt["revived"].append({"seat": t, "name": tp["name"]})
                rpt["notes"].append(f"叶澜依祝福：{label(tp)} 白天复活（技能重置）")
                log(g, f"叶澜依复活了 {pub_label(tp)}")

    # 9. 信息类（死亡结算后）；被禁足者不给信息
    def passive_ok(p):
        if p["seat"] in voided:
            rpt["notes"].append(f"{label(p)} 被禁足：今晚不结算其信息")
            return False
        return True

    first = n == 1
    if first:
        hb = role_in_play(g, "huanbi")
        if hb and hb["alive"] and not hb.get("is_favored") and passive_ok(hb):
            nn = len(g["seats"])
            demons = [q for q in g["seats"] if q["is_demon"]]
            minions = [q for q in g["seats"]
                       if ROLE_BY_ID[q["role"]]["team"] == "minion" or q["flags"].get("duishi")]
            dist = None
            if demons and minions:
                dist = min(circ_dist(nn, d["seat"] - 1, m["seat"] - 1)
                           for d in demons for m in minions)
            rpt["packets"].append({
                "seat": hb["seat"], "role": "huanbi", "kind": "huanbi_dist",
                "lines": [f"恶魔与最近爪牙的间距：{dist if dist is not None else '场上无爪牙，任意给数'}"],
                "malfunction": is_impaired(g, hb)})

        jf = role_in_play(g, "jingfei")
        if jf and jf["alive"] and not jf.get("is_favored") and passive_ok(jf):
            cnt = sum(1 for q in g["seats"] if ROLE_BY_ID[q["role"]]["non_male"])
            rpt["packets"].append({
                "seat": jf["seat"], "role": "jingfei", "kind": "jingfei_count",
                "lines": [f"场上非男性角色数量：{cnt}（含苏培盛/温实初口径）"],
                "malfunction": is_impaired(g, jf)})

        s = collected("bind")
        if s:
            yr = actor(s)
            t = targets(s)[0]
            yr["flags"]["bond"] = t
            tp = get_p(g, t)
            rpt["packets"].append({
                "seat": yr["seat"], "role": "yurao", "kind": "yurao_bind", "ctx": {"target": t},
                "lines": [f"告知玉娆：{pub_label(tp)} 是 {ROLE_BY_ID[tp['role']]['name']}（羁绊绑定）"],
                "malfunction": is_impaired(g, yr)})

    xyz = role_in_play(g, "xiaoyunzi")
    if xyz and xyz["alive"] and not xyz.get("is_favored") and passive_ok(xyz):
        left, right = living_neighbors(g, xyz["seat"])
        if left and right and left is not right:
            same = left["alignment"] == right["alignment"]
            rpt["packets"].append({
                "seat": xyz["seat"], "role": "xiaoyunzi", "kind": "xiaoyunzi",
                "ctx": {"left": left["seat"], "right": right["seat"]},
                "lines": [f"左邻 {pub_label(left)} 与右邻 {pub_label(right)}："
                          f"{'同一阵营' if same else '不同阵营'}"],
                "malfunction": is_impaired(g, xyz)})

    s = collected("jinxi")
    if s:
        jx = actor(s)
        if jx["alive"]:
            t1, t2 = targets(s)
            has_demon = any(get_p(g, t)["is_demon"] for t in (t1, t2))
            align_note = "（槿汐已对食为爪牙，信息由邪恶阵营运用）" if jx["flags"].get("duishi") else ""
            rpt["packets"].append({
                "seat": jx["seat"], "role": "jinxi", "kind": "jinxi", "ctx": {"t1": t1, "t2": t2},
                "lines": [f"{pub_label(get_p(g, t1))} 与 {pub_label(get_p(g, t2))} 中"
                          f"{'有' if has_demon else '没有'}恶魔{align_note}"],
                "malfunction": is_impaired(g, jx)})

    s = collected("check")
    if s:
        wsc = actor(s)
        if wsc["alive"]:
            t = targets(s)[0]
            tp = get_p(g, t)
            impaired_t = is_impaired(g, tp)
            lines = [f"{pub_label(tp)} {'处于' if impaired_t else '不处于'}中毒/醉酒状态"]
            if is_impaired(g, wsc):
                lines.append("温实初自身醉/毒：查验结果可为假，解除无效")
            elif impaired_t and s["collected"].get("cure", True):
                tp["statuses"] = [st for st in tp["statuses"]
                                  if st["type"] not in ("drunk", "poisoned")]
                if is_impaired(g, tp):
                    lines.append("注意：其醉酒来自浣碧邻座果郡王的座位效应，无法解除")
                else:
                    lines.append("已为其解除中毒/醉酒状态")
                    rpt["notes"].append(f"温实初解除了 {pub_label(tp)} 的异常状态")
            rpt["packets"].append({"seat": wsc["seat"], "role": "wenshichu",
                                   "kind": "check", "ctx": {"target": t},
                                   "lines": lines, "malfunction": is_impaired(g, wsc)})

    # 10. 华妃一丈红
    s = collected("huafei")
    if s:
        hf = actor(s)
        if hf["alive"]:
            t = targets(s)[0]
            if alive_count(g) <= 3:
                rpt["notes"].append("场上仅剩3人：华妃一丈红失效")
            elif is_impaired(g, hf):
                rpt["notes"].append(f"华妃醉/毒：一丈红无效 {impair_tag(g, hf)}")
            else:
                add_status(get_p(g, t), "marked", "huafei", "dusk", n)
                rpt["notes"].append(f"华妃赐 {pub_label(get_p(g, t))} 一丈红：次日其发起提名即暴毙")

    check_win(g, rpt)
    g.pop("_answers", None)
    return rpt


def _commit_night(g):
    n = g["night_number"]
    g["pending_night_events"] = []
    # 守护记录
    for p in g["seats"]:
        if "pending_last_protect" in p["flags"]:
            p["flags"]["last_protect"] = p["flags"].pop("pending_last_protect")
        elif p["role"] == "guojunwang":
            p["flags"].pop("last_protect", None)
    # 宠妃怀胎计数
    for p in g["seats"]:
        if p["is_favored"] and p["alive"]:
            p["favored_nights"] += 1
            if p["favored_nights"] >= 3 and not g.get("baby"):
                g["baby"] = {"type": None, "placed": False}
                log(g, f"宠妃已存在三个夜晚：皇上获得宝宝（类型由说书人决定）")
    prune_statuses(g, "dawn", n)
    g["night_state"] = None
    if g.get("winner"):
        return
    g["phase"] = "day"
    g["day_number"] = n
    g["day_state"] = {
        "number": n, "nominators": [], "nominees": [],
        "nominations": [], "private_chats": [], "silenced": None,
        "open_nomination": None, "execution_done": False, "ended": False,
        "used_actions": {},
    }
    log(g, f"第 {n} 个白天开始，存活 {alive_count(g)} 人，处决门槛 {vote_threshold(g)} 票")


# ---------------- 白天 ----------------

def day_menus(game):
    """每个存活玩家可用的白天行动。"""
    if game.get("phase") != "day":
        return {}
    ds = game["day_state"]
    menus = {}
    for p in game["seats"]:
        acts = []
        if not p["alive"]:
            menus[p["seat"]] = acts
            continue
        used = ds["used_actions"].get(str(p["seat"]), [])
        chatted = p["seat"] in ds["private_chats"]
        favored = p.get("is_favored")
        acts.append({"id": "private_chat", "label": "取消私聊标记" if chatted else "标记：私聊过说书人",
                     "needs_target": False})
        if p["role"] == "dunqinwang" and not favored and "force_drunk" not in used:
            acts.append({"id": "force_drunk", "label": "把酒言欢：强制一人醉酒一天一夜",
                         "needs_target": True})
        if p["role"] == "qiguiren" and not favored and "accuse" not in used:
            acts.append({"id": "accuse", "label": "公开检举一名玩家身份",
                         "needs_target": True, "needs_role_guess": True})
        if p["role"] == "qifei" and not favored and "silence" not in used:
            acts.append({"id": "silence", "label": "翠果，打烂她的嘴：禁言一名玩家",
                         "needs_target": True})
        if ds["silenced"] and ds["silenced"] == p["seat"]:
            acts.append({"id": "violation", "label": "该玩家违规说话 → 直接处决",
                         "needs_target": False, "danger": True})
        if p["role"] == "kuangtu":
            acts.append({"id": "direct_execute", "label": "狂徒被捉奸伏诛 / 身份暴露 → 处决",
                         "needs_target": False, "danger": True})
        else:
            acts.append({"id": "direct_execute", "label": "说书人直接处决此玩家",
                         "needs_target": False, "danger": True})
        menus[p["seat"]] = acts
    return menus


def _mark_used(ds, seat, action):
    ds["used_actions"].setdefault(str(seat), []).append(action)


def day_action(game, seat, action, params):
    if game.get("phase") != "day":
        raise ValueError("当前不在白天")
    ds = game["day_state"]
    p = get_p(game, seat)
    result = {"events": []}

    if action == "private_chat":
        if seat in ds["private_chats"]:
            ds["private_chats"].remove(seat)
            result["events"].append(f"{pub_label(p)} 取消私聊标记")
        else:
            ds["private_chats"].append(seat)
            result["events"].append(f"{pub_label(p)} 已标记：私聊过说书人")
        return result

    if not p["alive"]:
        raise ValueError("死亡玩家不能行动")

    if action == "force_drunk":
        t = get_p(game, int(params["target"]))
        _mark_used(ds, seat, action)
        if is_impaired(game, p):
            result["events"].append(f"敦亲王自身醉/毒：灌酒无效（但仍公开喊出台词）")
        else:
            add_status(t, "drunk", "dunqinwang", "dawn", game["night_number"] + 1)
            result["events"].append(f"敦亲王灌酒：{pub_label(t)} 醉酒一天一夜")
        log(game, result["events"][-1])

    elif action == "accuse":
        t = get_p(game, int(params["target"]))
        guess = params["role_guess"]
        _mark_used(ds, seat, action)
        if is_impaired(game, p):
            result["events"].append("祺贵人醉/毒：检举无事发生（对错均无效）")
        else:
            correct = t["role"] == guess or t["original_role"] == guess
            if correct:
                game["pending_night_events"].append({"type": "qiguiren_correct"})
                result["events"].append(
                    f"祺贵人检举 {pub_label(t)} 是 {ROLE_BY_ID[guess]['name']}：猜对了！"
                    "今夜将随机死亡一名玩家（结算时说书人选择）")
            else:
                game["pending_night_events"].append({"type": "qiguiren_wrong"})
                result["events"].append(
                    f"祺贵人检举 {pub_label(t)} 是 {ROLE_BY_ID[guess]['name']}：猜错了，祺贵人今夜暴毙")
        log(game, result["events"][-1])

    elif action == "silence":
        t = get_p(game, int(params["target"]))
        if p["flags"].get("last_silence") == t["seat"]:
            raise ValueError("不可对同一位玩家连续使用禁言")
        ds["silenced"] = t["seat"]
        p["flags"]["last_silence"] = t["seat"]
        add_status(t, "silenced", "qifei", "dusk", game["day_number"])
        _mark_used(ds, seat, action)
        result["events"].append(f"齐妃禁言 {pub_label(t)}：其今天不可说话，违规将被直接处决")
        log(game, result["events"][-1])

    elif action == "violation":
        result["events"].append(f"{pub_label(p)} 违规说话，被直接处决！")
        log(game, result["events"][-1])
        _execute(game, seat, result)

    elif action == "direct_execute":
        result["events"].append(f"说书人处决 {pub_label(p)}")
        log(game, result["events"][-1])
        _execute(game, seat, result)

    else:
        raise ValueError("未知行动")
    return result


def _execute(game, seat, result):
    """处决（投票或直接处决）。处决后当天结束。"""
    p = get_p(game, seat)
    ds = game["day_state"]
    # 胧月圣徒判定（宠妃化后失去原技能）
    if p["role"] == "longyue" and not p.get("is_favored") and not is_impaired(game, p):
        game["winner"] = "evil"
        game["win_reason"] = "胧月被处决：善良阵营失败"
        game["phase"] = "ended"
        p["alive"] = False
        result["events"].append("胧月被处决！善良阵营立即失败")
        log(game, game["win_reason"])
        return
    if p["role"] == "longyue" and not p.get("is_favored"):
        result["events"].append("胧月醉/毒时被处决：不触发阵营失败")

    rpt = {"deaths": [], "revived": [], "packets": [], "notes": []}
    _kill(game, rpt, seat, "execution")
    result["events"].extend(rpt["notes"])
    ds["execution_done"] = True
    ds["ended"] = True
    check_win(game)
    if not game.get("winner"):
        result["events"].append("处决完成，当天立即入夜（点击「进入夜晚」）")


def nominate(game, nominator, nominee):
    if game.get("phase") != "day":
        raise ValueError("当前不在白天")
    ds = game["day_state"]
    if ds["ended"]:
        raise ValueError("今天已处决，无法再提名")
    if ds["open_nomination"]:
        raise ValueError("有一个提名正在投票中")
    np_, ne = get_p(game, nominator), get_p(game, nominee)
    if not np_["alive"] or not ne["alive"]:
        raise ValueError("提名者与被提名者都必须存活")
    if nominator in ds["nominators"]:
        raise ValueError(f"{pub_label(np_)} 今天已提名过")
    if nominee in ds["nominees"]:
        raise ValueError(f"{pub_label(ne)} 今天已被提名过")

    result = {"events": [], "proceed_to_vote": False}
    ds["nominators"].append(nominator)

    # 华妃一丈红：提名者暴毙
    if has_status(np_, "marked") and alive_count(game) > 3:
        np_["alive"] = False
        result["events"].append(
            f"{pub_label(np_)} 发起提名的瞬间暴毙（一丈红）！提名作废，白天继续")
        log(game, f"一丈红发作：{label(np_)} 暴毙")
        rpt = {"deaths": [], "revived": [], "packets": [], "notes": []}
        # 走连锁（不算处决）：狂徒/孙答应/年羹尧等
        np_["alive"] = True  # 重置后走统一 kill 流程
        _kill(game, rpt, nominator, "ability")
        result["events"].extend(rpt["notes"])
        check_win(game)
        return result

    ds["nominees"].append(nominee)

    # 纯元皇后：首次被提名（宠妃化后失去原技能）
    if (ne["role"] == "chunyuan" and not ne.get("is_favored")
            and not ne["flags"].get("chunyuan_spent")):
        ne["flags"]["chunyuan_spent"] = True
        if is_impaired(game, ne):
            result["events"].append("纯元皇后醉/毒：首次被提名不触发（技能已消耗），正常进入投票")
        elif ROLE_BY_ID[np_["role"]]["team"] == "townsfolk":
            result["events"].append(
                f"白月光显灵！提名者 {pub_label(np_)} 是村民 → 立即被处决，当天直接入夜")
            log(game, f"纯元皇后触发：{label(np_)} 被处决")
            _execute(game, nominator, result)
            return result
        else:
            result["events"].append("纯元皇后首次被提名，但提名者不是村民：无事发生（技能已消耗），进入投票")

    ds["open_nomination"] = {"nominator": nominator, "nominee": nominee}
    result["proceed_to_vote"] = True
    result["threshold"] = vote_threshold(game)
    log(game, f"{pub_label(np_)} 提名 {pub_label(ne)}")
    return result


def vote(game, votes):
    ds = game["day_state"]
    if not ds.get("open_nomination"):
        raise ValueError("没有进行中的提名")
    nom = ds["open_nomination"]
    ne = get_p(game, nom["nominee"])
    threshold = vote_threshold(game)
    valid = []
    ghost_spent = []
    for v in votes:
        vp = get_p(game, int(v))
        if vp["alive"]:
            valid.append(vp["seat"])
        elif vp["ghost_vote"]:
            vp["ghost_vote"] = False
            valid.append(vp["seat"])
            ghost_spent.append(vp["seat"])
    passed = len(valid) >= threshold
    ds["nominations"].append({**nom, "votes": valid, "passed": passed})
    ds["open_nomination"] = None
    result = {"events": [], "votes": len(valid), "threshold": threshold, "passed": passed}
    if ghost_spent:
        result["events"].append(
            "消耗幽灵票：" + "、".join(pub_label(get_p(game, s)) for s in ghost_spent))
    if passed:
        result["events"].append(
            f"{len(valid)} 票 ≥ 门槛 {threshold}：{pub_label(ne)} 立即被处决！")
        log(game, f"投票通过（{len(valid)}/{threshold}）：{pub_label(ne)} 被处决")
        _execute(game, nom["nominee"], result)
    else:
        result["events"].append(
            f"{len(valid)} 票 < 门槛 {threshold}：{pub_label(ne)} 不死，白天继续")
        log(game, f"投票未过（{len(valid)}/{threshold}）：{pub_label(ne)} 存活")
    return result


def end_day(game):
    if game.get("phase") != "day":
        raise ValueError("当前不在白天")
    ds = game["day_state"]
    game["last_private_chats"] = list(ds["private_chats"])
    prune_statuses(game, "dusk", game["day_number"])
    game["night_number"] += 1
    game["phase"] = "night"
    game["day_state"] = None
    log(game, f"第 {game['night_number']} 个夜晚开始")
    build_night_steps(game)
    return game


# ---------------- 对外视图 ----------------

def game_view(game):
    view = deepcopy(game)
    view.pop("_answers", None)
    if game.get("phase") == "day":
        view["day_menus"] = day_menus(game)
        view["threshold"] = vote_threshold(game)
    view["alive_count"] = alive_count(game) if game.get("phase") not in (None, "prepare") else None
    return view
