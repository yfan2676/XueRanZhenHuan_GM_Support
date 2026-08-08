"""血战甄嬛传 —— 游戏引擎：夜晚向导、白天行动、提名投票、胜负判定。

时间模型：夜 n → 天 n → 夜 n+1 …

夜晚死亡模型：夜里的死亡**不当场生效**，而是进入 deferred 队列，等整个夜晚行动顺序
走完后由 _apply_dawn 统一落下（"破晓结算"）。因此天黑时还活着的玩家，今夜的技能一律
照常完整结算，信息也按"无人死亡"的盘面计算。唯一的例外是夜初就要处理的狂徒连锁
（孙答应死亡 → 全村民醉酒），它需要覆盖今夜。详见 docs/rules.md「夜晚行动顺序」。

状态到期点 (e, n)：e="dawn"（夜 n 结束时清除）或 e="dusk"（天 n 结束时清除）。
- 安陵容毒（夜 n 下）：至 dusk n（覆盖当夜+次日白天）
- 敦亲王灌酒（天 n）：至 dawn n+1
- 孙答应连锁全村醉：至 dawn n+1
- 一丈红（夜 n 赐）：至 dusk n（次日白天生效）
- 禁言（天 n）：至 dusk n
（守护与禁足不是状态：只在当夜结算内生效，用局部变量记录。）

夜晚行动顺序以 docs/rules.md「七、夜晚行动顺序」为唯一权威，
tests/test_night_order.py 会断言这里的步骤顺序与文档一致。
"""

import math
import random
from copy import deepcopy

from .roles import ROLE_BY_ID

DEMON_KINDS_HUANGSHANG_LINE = {"huangshang", "nvhuang", "basic"}


class NeedDecision(Exception):
    """结算过程中需要裁定。

    gm=True：说书人自己裁定（可随机）。
    gm=False：本质是某个玩家的选择，只是通过说书人的界面录入，不可随机
              （无说书人模式下这些会被转交给该玩家，见 auto._player_decision_seat）。
    """

    def __init__(self, key: str, prompt: str, options: list, gm: bool = True):
        super().__init__(prompt)
        self.key = key
        self.prompt = prompt
        self.options = options  # [{value, label}]
        self.gm = gm


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


def memo(game, tag, items):
    """追加一组说书人备忘（保密）。

    与 log（事件记录）分开：事件记录是流水账，备忘是结算时产生的、只有说书人该知道的
    判定细节（技能落空、连锁、反杀、继任……）。按事件顺序成组存放，tag 形如 夜1 / 白天2；
    同一事件内多次追加（一个白天里处决两次）合并进同一组。
    """
    items = [x for x in items if x]
    if not items:
        return
    groups = game.setdefault("memos", [])
    if groups and groups[-1]["tag"] == tag:
        groups[-1]["items"].extend(items)
    else:
        groups.append({"tag": tag, "items": list(items)})


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
        "memos": [],
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

def _opt(p, note="", avoid=False):
    """一个可选目标。

    死亡玩家一律照常列出、可以选中：界面不替说书人/玩家做合法性判断，技能照常发动，
    只是在破晓结算时不产生效果（信息类技能则明确回报"无效"）。仅在选项上标注"已死"。

    avoid：只影响随机（说书人的"🎲 随机"按钮与无说书人模式的机器人共用同一策略，
    见 docs/decision_maps.md）——死者一律不进随机池，信息类步骤可再传入额外的
    排除条件（如安陵容不给邪恶阵营、不重复）。被 avoid 的选项依然照常显示、
    可以手动点选。骰子不做规则判断。
    """
    dead = not p.get("alive", True)
    if dead:
        note = f"已死 · {note}" if note else "已死"
    return {"seat": p["seat"], "label": pub_label(p), "note": note,
            "avoid": dead or bool(avoid)}


def _dead_target(g, rpt, seat, what):
    """目标已死亡 → 记一条说明并返回 True（技能照常消耗，只是落空）。"""
    p = get_p(g, seat)
    if p["alive"]:
        return False
    rpt["notes"].append(f"{what}：目标 {pub_label(p)} 已死亡，技能照常发动但落空")
    return True


def _step(sid, kind, actor, title, prompt, options, count=1, optional=False, extras=None,
          gm=False):
    """gm=True 表示这一步是**说书人自己**做的选择（发什么情报），界面会给"🎲 随机"。

    默认 False：绝大多数步骤录入的是玩家自己指的目标，说书人只是代录，不能替玩家掷骰。
    """
    return {
        "id": sid, "kind": kind,
        "seat": actor["seat"] if actor else None,
        "actor": label(actor) if actor else None,
        "title": title, "prompt": prompt,
        "pick": {"count": count, "options": options},
        "optional": optional,
        "extras": extras or {},
        "gm": gm,
        "collected": None,
    }


def build_night_steps(game):
    """构建当夜的行动步骤。

    行动者必须存活（死人不再醒来），但**目标一律不做存活过滤**：死亡玩家照常出现在
    选项里、可以被选中，只是结算时落空。界面不替说书人/玩家判断"这个人不能选"。
    唯一的例外是三阿哥——"左右两边其中一名存活玩家"里的"存活"是技能文本本身，
    死者按官方邻座规则顺延跳过（见 docs/rules.md 三阿哥条目）。
    """
    n = game["night_number"]
    first = n == 1
    steps = []
    alive = alive_players(game)
    everyone = game["seats"]

    def find(rid):
        for p in alive:
            if p["role"] == rid and not p.get("is_favored"):
                return p
        return None

    fav = next((p for p in alive if p["is_favored"]), None)

    # 宠妃怀胎：转化当夜记为第 1 夜，进入第 3 个夜晚时皇上即拿到宝宝，**当夜就能放**。
    # favored_nights 记的是「已经过完的夜数」，所以今夜是第 favored_nights + 1 夜。
    if fav and fav["favored_nights"] + 1 >= 3 and not game.get("baby"):
        game["baby"] = {"type": None, "placed": False}
        log(game, "宠妃已存在三个夜晚：皇上获得宝宝（类型由说书人决定），今夜即可放置")

    if first:
        evil = [p for p in game["seats"] if p["alignment"] == "evil"]
        info = "、".join(label(p) for p in evil)
        steps.append(_step("evil_info", "info", None, "邪恶阵营互认",
                           f"唤醒邪恶阵营互认：{info}", [], count=0))

    # 禁足排在最前：被禁足者今夜的一切行动与信息都不结算，说书人先问宠妃才知道该跳过谁。
    if fav:
        steps.append(_step("jinzu", "pick", fav, "宠妃 · 禁足",
                           "使一名玩家当晚无法行动（连续同一人无效），或无行动",
                           [_opt(p, "上晚已禁足：连选无效" if p["seat"] == fav["flags"].get("last_jinzu") else "")
                            for p in everyone if p is not fav], optional=True))

    alr = find("anlingrong")
    if alr:
        females = [p for p in everyone
                   if ROLE_BY_ID[p["role"]]["gender"] == "F" and p is not alr]
        # 随机策略（docs/decision_maps.md）：不选死者/邪恶阵营/已告知过的；
        # 合格者全部告知过后回到真随机（仅仍排除死者），并提示说书人缘由。
        told = set(alr["flags"].get("alr_told", []))

        def _alr_excluded(p):
            return p["alignment"] == "evil" or p["seat"] in told

        exhausted = all(not p["alive"] or _alr_excluded(p) for p in females)
        prompt = "说书人选择一名女性角色告知安陵容（真实信息）"
        if exhausted and females:
            prompt += "——合格情报对象已全部告知过，本次随机为真随机"
        steps.append(_step("alr_info", "pick", alr, "安陵容 · 情报", prompt,
                           [_opt(p, ROLE_BY_ID[p["role"]]["name"]
                                 + ("（已告知过）" if p["seat"] in told else ""),
                                 avoid=not exhausted and _alr_excluded(p))
                            for p in females],
                           gm=True))
        steps.append(_step("alr_poison", "pick", alr, "安陵容 · 香粉下毒",
                           "选择一名女性角色下毒（至下一个黄昏），或无行动",
                           [_opt(p) for p in females], optional=True))

    # 太医紧跟在安陵容之后、果郡王之前：解毒当晚就能让后续技能（守护、槿汐、小允子……）
    # 恢复真实结算，这正是"解除后当晚后续行动即生效"这句规则的意义所在。
    wsc = find("wenshichu")
    if wsc:
        steps.append(_step("check", "pick", wsc, "温实初 · 太医",
                           "查验一名玩家是否中毒/醉酒；若是，结算时再问你解不解",
                           [_opt(p) for p in everyone if p is not wsc]))

    # 首夜同样唤醒：首夜没有刀，但雨露均沾首夜可用，"免睡"这一半是有意义的。
    gjw = find("guojunwang")
    if gjw:
        lastp = gjw["flags"].get("last_protect")
        steps.append(_step("protect", "pick", gjw, "果郡王 · 守护",
                           "守护一名玩家（只免恶魔的刀与侍寝；不挡禁足、宝宝、一丈红、处决），"
                           "可守自己，连守同一人无效",
                           [_opt(p, "上晚已守：连守无效" if p["seat"] == lastp else "")
                            for p in everyone], optional=True))

    demon = next((p for p in alive if p["is_demon"]), None)
    if demon:
        kind = demon["demon_kind"]
        others = [p for p in everyone if p is not demon]
        if kind == "huangshang":
            if not demon["flags"].get("yulu_used"):
                steps.append(_step("yulu", "pick", demon, "皇上 · 雨露均沾（成功仅一次）",
                                   "选择一名玩家侍寝：女性角色将转化为宠妃；"
                                   "失败不消耗机会（不反馈失败原因），或无行动",
                                   [_opt(p) for p in others], optional=True))
            if game.get("baby") and not game["baby"]["placed"]:
                steps.append(_step("baby", "pick", demon, "皇上 · 放置宝宝",
                                   "将宝宝放置于一名玩家身上（类型由说书人决定），或暂不放置",
                                   [_opt(p) for p in everyone], optional=True))
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
                               [_opt(p, "自刀传位" if p is demon else "") for p in everyone]))

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

    yly = find("yelanyi")
    if yly and not yly["flags"].get("revive_used"):
        if any(not p["alive"] for p in everyone):
            steps.append(_step("revive", "pick", yly, "叶澜依 · 你的福气在后头（全局一次）",
                               "祝福一名死亡玩家：破晓复活、仅一次技能全部重置（今夜不行动），或无行动",
                               [_opt(p, "" if not p["alive"] else "尚存活，复活无效")
                                for p in everyone], optional=True))

    # 被动信息角色同样进清单：面板即完整唤醒清单（内容在结算时生成，此处只确认唤醒）。
    if first:
        hb = find("huanbi")
        if hb:
            steps.append(_step("huanbi_info", "info", hb, "浣碧 · 首席丫鬟（被动）",
                               "唤醒浣碧：告知恶魔与最近爪牙的间距（内容在结算报告中生成）",
                               [], count=0))
        jf = find("jingfei")
        if jf:
            steps.append(_step("jingfei_info", "info", jf, "敬妃 · 砖妃数砖（被动）",
                               "唤醒敬妃：告知场上非男性角色数量（内容在结算报告中生成）",
                               [], count=0))

    if first:
        yr = find("yurao")
        if yr:
            goods = [p for p in everyone if p["alignment"] == "good" and p is not yr]
            steps.append(_step("bind", "pick", yr, "玉娆 · 姐妹影分身",
                               "说书人选择一名善良玩家告知玉娆（其被恶魔夜杀时玉娆陪葬）",
                               [_opt(p, ROLE_BY_ID[p["role"]]["name"]) for p in goods],
                               gm=True))

    xyz = find("xiaoyunzi")
    if xyz:
        steps.append(_step("xiaoyunzi_info", "info", xyz, "小允子 · 邻座情报（被动）",
                           "唤醒小允子：告知左右存活邻座是否同一阵营（内容在结算报告中生成）",
                           [], count=0))

    jx = find("jinxi")
    if jx:
        steps.append(_step("jinxi", "pick", jx, "槿汐姑姑 · 打听小道消息",
                           "选择两名玩家，得知其中是否有恶魔",
                           [_opt(p) for p in everyone if p is not jx], count=2))

    hf = find("huafei")
    if hf:
        note = "仅剩3人，技能失效" if alive_count(game) <= 3 else ""
        steps.append(_step("huafei", "pick", hf, "华妃 · 一丈红",
                           f"赐一名玩家一丈红（次日其发起提名即暴毙），或无行动 {note}",
                           [_opt(p) for p in everyone if p is not hf], optional=True))

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

def _need(answers, key, prompt, options, gm=True):
    if key in answers:
        return answers[key]
    raise NeedDecision(key, prompt, options, gm)


def _seat_options(g, seats=None):
    """裁定用的座位选项：死者同样列出（标注"已死"），由说书人/玩家自行判断。

    avoid 同 _opt：死者不进"🎲 随机"的池子，但仍可手动点选。
    """
    return [{"value": q["seat"],
             "label": pub_label(q) + ("（已死）" if not q["alive"] else ""),
             "avoid": not q["alive"]}
            for q in (seats if seats is not None else g["seats"])]


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
        return {"pending": {"key": d.key, "prompt": d.prompt, "options": d.options,
                            "gm": d.gm}}, None
    rpt["winner"] = g.get("winner")
    rpt["win_reason"] = g.get("win_reason")
    if commit:
        # 只在真正落地时记进备忘：预览可以反复重算
        memo(g, f"夜{g['night_number']}", rpt["notes"])
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
            opts = _seat_options(g)
            if opts:
                tgt = _need(g["_answers"], f"nian_{seat}",
                            f"年羹尧（{pub_label(p)}）死亡，选择带走一名玩家", opts, gm=False)
                if not _dead_target(g, rpt, int(tgt), "年羹尧带人"):
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

    # 当夜产生的死亡与身份变更一律推迟到「破晓」统一结算（_apply_dawn）：
    # 夜里被杀的玩家，其技能在死亡生效前照常完整结算，信息也按「无人死亡」的盘面计算。
    # 例外：上个白天遗留的狂徒连锁（孙答应死亡 → 全村民醉酒）仍在夜初结算，见下方 0。
    deferred = []

    def defer_kill(seat, cause, killer=None):
        deferred.append({"kind": "kill", "seat": int(seat), "cause": cause,
                         "killer": killer["seat"] if killer else None})

    def collected(kind):
        s = next((x for x in ns["steps"] if x["id"] == kind), None)
        if not s or not s["collected"] or s["collected"].get("no_action"):
            return None
        if s["seat"] in voided:
            rpt["notes"].append(
                f"{s['title']}：{pub_label(get_p(g, s['seat']))} 被宠妃禁足，今夜行动不结算")
            return None
        return s

    def actor(s):
        return get_p(g, s["seat"])

    def targets(s):
        return [int(t) for t in s["collected"]["targets"]]

    # 0. 上白天遗留的夜晚事件
    #    孙答应连锁在夜初立即结算（其死亡导致的全村民醉酒需覆盖今夜）；
    #    其余「今夜死亡」一律入破晓队列，让当事人先用完自己的夜晚技能。
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
                defer_kill(qg["seat"], "ability")
        elif ev["type"] == "qiguiren_correct":
            tgt = _need(answers, "qiguiren_victim",
                        "祺贵人检举猜对：说书人选择今夜随机死亡的一名玩家",
                        _seat_options(g))
            if not _dead_target(g, rpt, int(tgt), "祺贵人检举猜对"):
                rpt["notes"].append(f"祺贵人检举猜对：{pub_label(get_p(g, int(tgt)))} 今夜死亡")
                defer_kill(int(tgt), "ability")
        elif ev["type"] == "nian_takealong":
            p = get_p(g, ev["seat"])
            opts = _seat_options(g)
            if opts:
                tgt = _need(answers, f"nian_{ev['seat']}",
                            f"年羹尧（{pub_label(p)}）白天死亡，今夜带走一名玩家", opts, gm=False)
                if not _dead_target(g, rpt, int(tgt), "年羹尧带人"):
                    rpt["notes"].append(f"年羹尧带走 {pub_label(get_p(g, int(tgt)))}")
                    defer_kill(int(tgt), "ability")

    # 1. 宠妃禁足（最先结算：voided 决定今夜谁的行动与信息一律不生效）
    #    禁足只锁"当晚行动"，不是保护——被禁足者照样会被刀、会被一丈红标记。
    s = collected("jinzu")
    if s:
        fav = actor(s)
        t = targets(s)[0]
        if is_impaired(g, fav):
            rpt["notes"].append(f"宠妃醉/毒：禁足无效 {impair_tag(g, fav)}")
        elif t == fav["flags"].get("last_jinzu"):
            rpt["notes"].append("宠妃连续两晚禁足同一人：禁足无效（规则禁止）")
        else:
            fav["flags"]["pending_last_jinzu"] = t
            if not _dead_target(g, rpt, t, "宠妃禁足"):
                voided.add(t)
                rpt["notes"].append(
                    f"宠妃禁足 {pub_label(get_p(g, t))}：其今夜的行动与信息一律不结算")

    # 2. 安陵容
    s = collected("alr_info")
    if s:
        alr = actor(s)
        t = get_p(g, targets(s)[0])
        told = alr["flags"].setdefault("alr_told", [])
        if t["seat"] not in told:
            told.append(t["seat"])
        rpt["packets"].append({
            "seat": alr["seat"], "role": "anlingrong", "kind": "alr_info",
            "lines": [f"告知安陵容：{pub_label(t)} 是 {ROLE_BY_ID[t['role']]['name']}"],
            "malfunction": is_impaired(g, alr)})
    s = collected("alr_poison")
    if s:
        alr = actor(s)
        t = targets(s)[0]
        if is_impaired(g, alr):
            rpt["notes"].append(f"安陵容醉/毒：下毒无效 {impair_tag(g, alr)}")
        elif _dead_target(g, rpt, t, "安陵容下毒"):
            pass
        else:
            add_status(get_p(g, t), "poisoned", "anlingrong", "dusk", n)
            rpt["notes"].append(f"安陵容对 {pub_label(get_p(g, t))} 下毒（至下个黄昏）")

    # 3. 温实初查验 / 解毒（排在果郡王之前：解毒后，今夜后续技能按"已恢复"结算）
    #    两段式：先出查验结果，再由温实初本人决定解不解——不知道结果就得先勾选是没道理的。
    s = collected("check")
    if s and actor(s)["alive"]:
        wsc = actor(s)
        t = targets(s)[0]
        tp = get_p(g, t)
        impaired_t = is_impaired(g, tp)
        lines = [f"{pub_label(tp)} {'处于' if impaired_t else '不处于'}中毒/醉酒状态"]
        if is_impaired(g, wsc):
            lines.append("温实初自身醉/毒：查验结果可为假，解除无效")
        elif impaired_t:
            cure = _need(answers, f"cure_{wsc['seat']}_{n}",
                         f"温实初查出 {pub_label(tp)} 处于中毒/醉酒状态，是否为其解除？",
                         [{"value": "yes", "label": f"解除 {pub_label(tp)} 的中毒/醉酒"},
                          {"value": "no", "label": "不解除（留着这条信息）"}],
                         gm=False)
            if cure == "yes":
                tp["statuses"] = [st for st in tp["statuses"]
                                  if st["type"] not in ("drunk", "poisoned")]
                if is_impaired(g, tp):
                    lines.append("注意：其醉酒来自浣碧邻座果郡王的座位效应，无法解除")
                else:
                    lines.append("已为其解除中毒/醉酒状态（今夜后续技能按已恢复结算）")
                    rpt["notes"].append(f"温实初解除了 {pub_label(tp)} 的异常状态")
            else:
                lines.append("你选择不解除")
        rpt["packets"].append({"seat": wsc["seat"], "role": "wenshichu",
                               "kind": "check", "ctx": {"target": t},
                               "lines": lines, "malfunction": is_impaired(g, wsc)})

    # 4. 果郡王守护
    protected = set()
    s = collected("protect")
    if s:
        gjw = actor(s)
        t = targets(s)[0]
        if is_impaired(g, gjw):
            rpt["notes"].append(f"果郡王醉/毒：守护无效 {impair_tag(g, gjw)}")
        elif t == gjw["flags"].get("last_protect"):
            rpt["notes"].append("果郡王连守同一人：守护无效（规则禁止）")
        elif _dead_target(g, rpt, t, "果郡王守护"):
            gjw["flags"]["pending_last_protect"] = t
        else:
            protected.add(t)
            gjw["flags"]["pending_last_protect"] = t
            rpt["notes"].append(f"果郡王守护 {pub_label(get_p(g, t))}（免刀免睡）")

    # 5. 雨露均沾
    # yulu_success 只记录「侍寝成功」的目标，供三阿哥捉奸判定使用。
    # 侍寝失败（目标为男性角色 / 被果郡王免睡 / 皇上醉毒）不构成捉奸：
    # 两人各自失败，无人死亡（例如两人同晚都挑了男性角色）。
    yulu_success = None
    s = collected("yulu")
    if s:
        hs = actor(s)
        t = targets(s)[0]
        tp = get_p(g, t)
        fail = None
        if is_impaired(g, hs):
            fail = f"皇上醉/毒：侍寝失败 {impair_tag(g, hs)}"
        elif not tp["alive"]:
            fail = f"目标 {pub_label(tp)} 已死亡：侍寝失败"
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
            yulu_success = t
            tp["is_favored"] = True
            tp["alignment"] = "evil"
            tp["favored_nights"] = 0
            rpt["notes"].append(f"雨露均沾：{label(tp)} 转化为宠妃（邪恶阵营，失去原技能，获得禁足）")
            rpt["packets"].append({"seat": hs["seat"], "role": "huangshang",
                                   "kind": "yulu", "ctx": {"target": t},
                                   "lines": [f"侍寝成功：{pub_label(tp)} 已转化为宠妃"],
                                   "malfunction": False})
            log(g, f"雨露均沾：{pub_label(tp)} 成为宠妃")

    # 6. 宝宝放置
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
            # 放在尸体上：宝宝照样消耗，只是炸了个寂寞
            if not _dead_target(g, rpt, t, "炸弹宝宝"):
                rpt["notes"].append(f"炸弹宝宝：{pub_label(get_p(g, t))} 当夜被炸死")
                defer_kill(t, "baby")
        else:
            fav = next((q for q in g["seats"] if q["is_favored"] and q["alive"]), None)
            if fav:
                rpt["notes"].append(f"狸猫宝宝：宠妃 {pub_label(fav)} 被炸死（狸猫换太子）")
                defer_kill(fav["seat"], "baby")

    # 7. 恶魔杀（此处只裁定结果，死亡与身份变更统一推迟到破晓）
    # demon_choice 记录皇上系恶魔当晚「沾」到的刀口（无论是否命中），供三阿哥捉奸判定使用
    demon_choice = None
    s = collected("kill")
    if s:
        d = actor(s)
        kind = d["demon_kind"]
        if is_impaired(g, d):
            rpt["notes"].append(f"恶魔醉/毒：今夜杀害无效 {impair_tag(g, d)}")
        elif kind == "taihou":
            t1, t2 = targets(s)
            allowed = [t for t in (t1, t2)
                       if t not in protected and get_p(g, t)["alive"]]
            for t in (t1, t2):
                if not get_p(g, t)["alive"]:
                    rpt["notes"].append(f"{pub_label(get_p(g, t))} 已死亡，这一选无效")
                elif t in protected:
                    rpt["notes"].append(f"{pub_label(get_p(g, t))} 被守护，不会死亡")
            opts = [{"value": "none", "label": "无人死亡"}]
            opts += [{"value": str(t), "label": f"仅 {pub_label(get_p(g, t))} 死亡"} for t in allowed]
            if len(allowed) == 2:
                opts.append({"value": "both", "label": "两人都死"})
            pick = _need(answers, "taihou_deaths",
                         f"太后选择了 {pub_label(get_p(g, t1))} 与 {pub_label(get_p(g, t2))}，几人死亡？", opts)
            chosen = [] if pick == "none" else allowed if pick == "both" else [int(pick)]
            for t in chosen:
                tp = get_p(g, t)
                # 反杀按"凶手是不是皇后"判定：继任太后线的皇后同样触发（rules.md 甄嬛条目）
                if (tp["role"] == "zhenhuan" and not tp.get("is_favored")
                        and d["role"] == "huanghou" and not is_impaired(g, tp)):
                    deferred.append({"kind": "zhenhuan_counter",
                                     "seat": t, "demon": d["seat"]})
                    rpt["notes"].append(
                        f"甄嬛反杀！{pub_label(tp)} 将于破晓成为女皇（邪恶新领袖，每晚一刀）；皇后技能永久失效")
                else:
                    if (tp["role"] == "zhenhuan" and d["role"] == "huanghou"
                            and is_impaired(g, tp)):
                        rpt["notes"].append("甄嬛醉/毒：反杀失效，正常死亡")
                    defer_kill(t, "demon_kill", killer=d)
        else:
            t = targets(s)[0]
            tp = get_p(g, t)
            if kind == "taishanghuang" and t == d["seat"]:
                chats = g.get("last_private_chats", [])
                opts = _seat_options(g, [get_p(g, c) for c in chats])
                if opts:
                    succ = int(_need(answers, "tsh_successor",
                                     "太上皇自刀传位：从上个白天私聊过说书人的玩家中选择新皇上",
                                     opts, gm=False))
                    deferred.append({"kind": "tsh_succession",
                                     "seat": d["seat"], "successor": succ})
                    rpt["notes"].append(
                        f"太上皇自刀传位：{pub_label(get_p(g, succ))} 将于破晓成为新皇上（完整皇上技能）")
                else:
                    rpt["notes"].append("太上皇自刀但上个白天无人私聊说书人：传位失败")
                    defer_kill(d["seat"], "demon_kill", killer=d)
            else:
                demon_choice = t
                if _dead_target(g, rpt, t, "恶魔夜杀"):
                    pass
                elif t in protected:
                    rpt["notes"].append(f"{pub_label(tp)} 被果郡王守护：今夜无人死于恶魔之手")
                elif (tp["role"] == "zhenhuan" and not tp.get("is_favored")
                      and (kind == "huangshang" or d["role"] == "huanghou")
                      and not is_impaired(g, tp)):
                    deferred.append({"kind": "zhenhuan_counter",
                                     "seat": t, "demon": d["seat"]})
                    rpt["notes"].append(
                        f"甄嬛反杀！{pub_label(tp)} 将于破晓成为女皇（邪恶新领袖，每晚一刀）；皇后技能永久失效")
                else:
                    if (tp["role"] == "zhenhuan" and is_impaired(g, tp)
                            and (kind == "huangshang" or d["role"] == "huanghou")):
                        rpt["notes"].append("甄嬛醉/毒：反杀失效，正常死亡")
                    defer_kill(t, "demon_kill", killer=d)

    # 8. 三阿哥染指
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
                defer_kill(sag["seat"], "ability")
            elif t == yulu_success or t == demon_choice:
                rpt["notes"].append(
                    "三阿哥与皇上同晚沾染同一目标：被捉奸，被皇上杀死"
                    "（夜杀按皇上选定的刀口判定，杀成与否不影响；侍寝则须成功转化才算撞人）")
                defer_kill(sag["seat"], "ability")
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
            # "女性爪牙"按阵营判定：对食转恶的槿汐同样给失败手势（rules.md 三阿哥条目）
            elif (r["gender"] == "M" or tp["role"] in ("taihou", "qifei", "longyue")
                  or (r["gender"] == "F" and tp["alignment"] == "evil")):
                rpt["packets"].append({"seat": sag["seat"], "role": "sanage",
                                       "kind": "sanage", "ctx": {"target": t},
                                       "lines": [f"手势：失败（目标 {pub_label(tp)} 不可染指）"],
                                       "malfunction": False})
            else:
                rpt["packets"].append({"seat": sag["seat"], "role": "sanage",
                                       "kind": "sanage", "ctx": {"target": t},
                                       "lines": [f"手势：成功（目标 {pub_label(tp)}，无额外效果）"],
                                       "malfunction": False})

    # 9. 叶澜依复活（此处只裁定成败；复活在破晓、死亡统一结算之后落地，
    #    被复活者今夜没有任何步骤，信息也不重发——见 rules.md 叶澜依条目）
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
                deferred.append({"kind": "revive", "seat": t})
                rpt["notes"].append(
                    f"叶澜依祝福：{label(tp)} 将于破晓复活（仅一次技能全部重置，今夜不行动）")

    # 10. 信息类（浣碧/敬妃/玉娆/小允子/槿汐）；被禁足者不给信息
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

    # 11. 华妃一丈红
    s = collected("huafei")
    if s:
        hf = actor(s)
        if hf["alive"]:
            t = targets(s)[0]
            if alive_count(g) <= 3:
                rpt["notes"].append("场上仅剩3人：华妃一丈红失效")
            elif is_impaired(g, hf):
                rpt["notes"].append(f"华妃醉/毒：一丈红无效 {impair_tag(g, hf)}")
            elif _dead_target(g, rpt, t, "华妃一丈红"):
                pass
            else:
                add_status(get_p(g, t), "marked", "huafei", "dusk", n)
                rpt["notes"].append(f"华妃赐 {pub_label(get_p(g, t))} 一丈红：次日其发起提名即暴毙")

    # 12. 破晓：所有夜晚技能结算完毕后，统一处理当夜死亡与身份变更
    _apply_dawn(g, rpt, deferred)

    check_win(g, rpt)
    g.pop("_answers", None)
    return rpt


def _apply_dawn(g, rpt, deferred):
    """破晓结算：夜晚技能一律先于死亡生效，此处才真正落死亡与继任/上位。

    死亡触发型技能（年羹尧带人、敬妃死讯、狂徒→孙答应连锁、皇后继任）在此刻链式触发；
    链上新产生的死亡直接递归处理——被链带走的玩家今夜的技能同样已经结算过了。
    """
    for item in deferred:
        kind = item["kind"]

        if kind == "kill":
            killer = get_p(g, item["killer"]) if item["killer"] else None
            _kill(g, rpt, item["seat"], item["cause"], killer=killer)

        elif kind == "zhenhuan_counter":
            zh, demon = get_p(g, item["seat"]), get_p(g, item["demon"])
            if not zh["alive"]:
                # 甄嬛当夜另有死因（如炸弹宝宝）：反杀仍带走恶魔，但无人上位为女皇
                rpt["notes"].append(f"甄嬛（{pub_label(zh)}）当夜已死：反杀带走恶魔，女皇之位空悬")
                _kill(g, rpt, demon["seat"], "counter", killer=zh)
                continue
            g["huanghou_disabled"] = True
            g["_suppress_succession"] = True
            _kill(g, rpt, demon["seat"], "counter", killer=zh)
            g["_suppress_succession"] = False
            zh.update({"is_demon": True, "demon_kind": "nvhuang",
                       "alignment": "evil", "role": "nvhuang"})
            log(g, f"甄嬛反杀：{pub_label(zh)} 成为女皇；皇后技能永久失效")

        elif kind == "revive":
            # 复活最后落地（revive 在步骤 9 才入队，天然排在所有刀之后）：
            # 死亡先结算，被复活者不会被今夜的刀再次带走
            tp = get_p(g, item["seat"])
            if not tp["alive"]:
                tp["alive"] = True
                tp["ghost_vote"] = True
                tp["flags"] = {}
                tp["statuses"] = []
                rpt["revived"].append({"seat": tp["seat"], "name": tp["name"]})
                log(g, f"叶澜依复活了 {pub_label(tp)}（仅一次技能重置）")

        elif kind == "tsh_succession":
            demon, sp = get_p(g, item["seat"]), get_p(g, item["successor"])
            if not sp["alive"]:
                rpt["notes"].append(f"太上皇传位失败：继任人选 {pub_label(sp)} 当夜同样死亡")
                _kill(g, rpt, demon["seat"], "demon_kill", killer=demon)
                continue
            g["_suppress_succession"] = True
            _kill(g, rpt, demon["seat"], "demon_kill", killer=demon)
            g["_suppress_succession"] = False
            sp.update({"is_demon": True, "demon_kind": "huangshang",
                       "alignment": "evil", "role": "huangshang"})
            log(g, f"太上皇传位：{pub_label(sp)} 成为新皇上")


def _commit_night(g):
    n = g["night_number"]
    g["pending_night_events"] = []
    # 守护 / 禁足记录：本夜指了谁就记下，没指就清空（"不可连续同一人"只锁相邻两夜）
    for p in g["seats"]:
        if "pending_last_protect" in p["flags"]:
            p["flags"]["last_protect"] = p["flags"].pop("pending_last_protect")
        elif p["role"] == "guojunwang":
            p["flags"].pop("last_protect", None)
        if "pending_last_jinzu" in p["flags"]:
            p["flags"]["last_jinzu"] = p["flags"].pop("pending_last_jinzu")
        elif p["is_favored"]:
            p["flags"].pop("last_jinzu", None)
    # 宠妃怀胎计数（宝宝的发放时点在 build_night_steps：满第 3 夜当夜即可放置）
    for p in g["seats"]:
        if p["is_favored"] and p["alive"]:
            p["favored_nights"] += 1
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


def _accuse_hits(t, guess) -> bool:
    """祺贵人检举是否猜中：以**现身份**为准，原角色一律算错。

    被雨露均沾转化的宠妃、甄嬛反杀上位的女皇、太上皇传位产生的新皇上，都已经不是原来
    那个角色了——检举"她是叶澜依"应当算猜错，只有猜中"宠妃"才算对（见 rules.md）。
    """
    if t.get("is_favored"):
        return guess == "chongfei"
    return t["role"] == guess


def _flush_day_notes(game, result):
    """白天产生的说书人备注统一落进备忘（保密）。

    events 是公开事件（两种模式都会广播给玩家）；notes 只进说书人备忘/隐藏日志。
    玩家该听到的必须显式放进 events——保密是默认，公开是刻意为之（rules.md 一.11）。
    """
    memo(game, f"白天{game['day_number']}", result.get("notes") or [])
    return result


def day_action(game, seat, action, params):
    if game.get("phase") != "day":
        raise ValueError("当前不在白天")
    ds = game["day_state"]
    p = get_p(game, seat)
    result = {"events": [], "notes": []}

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
        # 公开事件在生效/失效两种情况下必须一字不差，否则当场暴露敦亲王的醉/毒状态
        result["events"].append(f"敦亲王把酒言欢：{pub_label(t)} 被灌醉一天一夜")
        if is_impaired(game, p):
            result["notes"].append("敦亲王自身醉/毒：灌酒实际无效（台词与公告照常）")
        else:
            add_status(t, "drunk", "dunqinwang", "dawn", game["night_number"] + 1)
        log(game, result["events"][-1])

    elif action == "accuse":
        t = get_p(game, int(params["target"]))
        guess = params["role_guess"]
        _mark_used(ds, seat, action)
        # 公开只播报检举宣言；对/错由当夜死亡揭晓——立刻公告结果会让
        # 醉/毒时的"无事发生"当场暴露祺贵人的状态（见 decision_maps.md §7）
        result["events"].append(
            f"祺贵人公开检举：{pub_label(t)} 是 {ROLE_BY_ID[guess]['name']}")
        if is_impaired(game, p):
            result["notes"].append("祺贵人醉/毒：检举无事发生（对错均无效，今夜无相应死亡）")
        else:
            if _accuse_hits(t, guess):
                game["pending_night_events"].append({"type": "qiguiren_correct"})
                result["notes"].append(
                    f"祺贵人猜对（{pub_label(t)} 确为 {ROLE_BY_ID[guess]['name']}）："
                    "今夜将随机死亡一名玩家（结算时选择）")
            else:
                game["pending_night_events"].append({"type": "qiguiren_wrong"})
                result["notes"].append("祺贵人猜错：今夜暴毙")
        log(game, result["events"][-1])

    elif action == "silence":
        t = get_p(game, int(params["target"]))
        if p["flags"].get("last_silence") == t["seat"]:
            raise ValueError("不可对同一位玩家连续使用禁言")
        ds["silenced"] = t["seat"]
        p["flags"]["last_silence"] = t["seat"]
        add_status(t, "silenced", "qifei", "dusk", game["day_number"])
        _mark_used(ds, seat, action)
        if t["alive"]:
            result["events"].append(f"齐妃禁言 {pub_label(t)}：其今天不可说话，违规将被直接处决")
        else:
            # 死者本就没有可被处决的东西：禁言照常消耗，但没有任何强制手段
            result["events"].append(
                f"齐妃禁言了已死亡的 {pub_label(t)}：无强制手段——"
                "其继续说话也不会被处决，幽灵票照常保留（本次禁言机会已消耗）")
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
    return _flush_day_notes(game, result)


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
        # 处决照常进行、公告如常：这条只有说书人该知道，公告会同时暴露角色与醉/毒状态
        result.setdefault("notes", []).append("胧月醉/毒时被处决：不触发阵营失败")

    rpt = {"deaths": [], "revived": [], "packets": [], "notes": []}
    _kill(game, rpt, seat, "execution")
    # 连锁死亡（处决者本人之外）是公开事实；结算细节只进备注
    for d in rpt["deaths"][1:]:
        result["events"].append(f"死亡：{d['seat']}号 {d['name']}")
    result.setdefault("notes", []).extend(rpt["notes"])
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
    if has_status(np_, "silenced"):
        raise ValueError(f"{pub_label(np_)} 被齐妃禁言：今日不得提名（仍可投票）")
    if nominator in ds["nominators"]:
        raise ValueError(f"{pub_label(np_)} 今天已提名过")
    if nominee in ds["nominees"]:
        raise ValueError(f"{pub_label(ne)} 今天已被提名过")

    result = {"events": [], "proceed_to_vote": False, "notes": []}
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
        for d in rpt["deaths"][1:]:
            result["events"].append(f"死亡：{d['seat']}号 {d['name']}")
        result["notes"].extend(rpt["notes"])
        check_win(game)
        return _flush_day_notes(game, result)

    ds["nominees"].append(nominee)

    # 纯元皇后：首次被提名（宠妃化后失去原技能）
    if (ne["role"] == "chunyuan" and not ne.get("is_favored")
            and not ne["flags"].get("chunyuan_spent")):
        ne["flags"]["chunyuan_spent"] = True
        if is_impaired(game, ne):
            # 不触发时对外静默（官方处女裁定）：公告会白白暴露纯元的身份与状态
            result["notes"].append("纯元皇后醉/毒：首次被提名不触发（技能已消耗），照常进入投票")
        elif ROLE_BY_ID[np_["role"]]["team"] == "townsfolk":
            result["events"].append(
                f"白月光显灵！提名者 {pub_label(np_)} 是村民 → 立即被处决，当天直接入夜")
            log(game, f"纯元皇后触发：{label(np_)} 被处决")
            _execute(game, nominator, result)
            return _flush_day_notes(game, result)
        else:
            result["notes"].append("纯元皇后首次被提名，但提名者不是村民：无事发生（技能已消耗），照常进入投票")

    ds["open_nomination"] = {"nominator": nominator, "nominee": nominee}
    result["proceed_to_vote"] = True
    result["threshold"] = vote_threshold(game)
    log(game, f"{pub_label(np_)} 提名 {pub_label(ne)}")
    return _flush_day_notes(game, result)


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
    result = {"events": [], "notes": [],
              "votes": len(valid), "threshold": threshold, "passed": passed}
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
    return _flush_day_notes(game, result)


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
