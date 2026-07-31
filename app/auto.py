"""无说书人（自动 GM）模式：房间、大厅、夜晚自动结算、白天提名投票。

设计要点：
- 完全复用 engine 的结算逻辑；说书人裁定点（NeedDecision）由机器人随机选择
  一个合法选项，属于玩家自身的决定（年羹尧带人、太上皇传位）转交对应玩家。
- 公开信息进 announcements（全员可见）；夜晚私信按座位进 private_log；
  醉/毒玩家的信息包由机器人伪造假信息（真人说书人的工作）。
- 身份 = 玩家名（私人局，约定不作弊；重连时任选自己的名字即可）。
"""

import random

from . import engine
from .roles import ROLE_BY_ID, ROLES, TEAM_NAMES
from .setups import generate
from .store import store

# 说书人代为选择/宣告的夜晚步骤（玩家无需操作）
AUTO_STEPS = {"evil_info", "alr_info", "bind"}

# 白天允许玩家自己触发的行动；direct_execute 仅狂徒本人可用（自曝伏诛）
ALLOWED_DAY = {"private_chat", "force_drunk", "accuse", "silence", "violation"}


# ---------------- 基础 ----------------

def get_room(rid):
    room = store.get(rid)
    if room is None or room.get("mode") != "auto":
        return None
    return room


def seat_of(room, name):
    for p in room["seats"]:
        if p.get("name") == name:
            return p
    raise ValueError("找不到该玩家名，请先入座/选择你的名字")


def announce(room, tag, text):
    # engine 的事件文案面向说书人，个别提示在无 GM 模式下不适用
    text = text.replace("（点击「进入夜晚」）", "")
    room["announcements"].append({"tag": tag, "text": text})


def private(room, seat, tag, lines):
    room["private_log"].setdefault(str(seat), []).append({"tag": tag, "lines": list(lines)})


# ---------------- 大厅 ----------------

def create_room(player_count):
    # 4 位数字房间号，方便口头传达/手机输入（私人局，撞号概率可忽略）
    rnd = random.Random()
    gid = None
    for _ in range(100):
        cand = str(rnd.randint(1000, 9999))
        if cand not in store.games:
            gid = cand
            break
    room = store.create(player_count, gid)
    room.update({
        "mode": "auto",
        "phase": "lobby",
        "ready": {},
        "announcements": [],
        "private_log": {},
        "hidden_log": [],
        "evil_roster": None,
        "night_pending": None,
        "night_answers": {},
        "vote_state": None,
        "end_day_votes": [],
    })
    store.update(room)
    return room


def join(room, name, seat):
    if room.get("phase") != "lobby":
        raise ValueError("对局已开始：直接在进入页选择你的玩家名即可回到对局")
    name = (name or "").strip()
    if not name:
        raise ValueError("请输入玩家名")
    if not (1 <= seat <= room["player_count"]):
        raise ValueError("座位号不存在")
    tgt = room["seats"][seat - 1]
    if tgt["name"] and tgt["name"] != name:
        raise ValueError(f"{seat} 号座位已被 {tgt['name']} 占用")
    for q in room["seats"]:
        if q["name"] == name and q["seat"] != seat:
            q["name"] = ""
            room["ready"].pop(str(q["seat"]), None)
    tgt["name"] = name
    room["ready"].pop(str(seat), None)
    store.update(room)
    return room


def set_ready(room, name, ready):
    if room.get("phase") != "lobby":
        raise ValueError("对局已开始")
    p = seat_of(room, name)
    room["ready"][str(p["seat"])] = bool(ready)
    if (all(q["name"] for q in room["seats"])
            and all(room["ready"].get(str(q["seat"])) for q in room["seats"])):
        return _begin(room)
    store.update(room)
    return room


def _begin(room):
    result = generate(room["player_count"], None, random.Random())
    for seat, rid in zip(room["seats"], result["roles"]):
        seat["role"] = rid
    room["setup_id"] = result["setup_id"]
    room["setup_name"] = result["setup_name"]
    room["phase"] = "prepare"
    engine.start_game(room)
    room["evil_roster"] = [
        {"seat": p["seat"], "name": p["name"], "role": ROLE_BY_ID[p["role"]]["name"]}
        for p in room["seats"] if p["alignment"] == "evil"]
    announce(room, "系统",
             f"全员就绪，游戏开始！板子：{result['setup_name']}。身份已发放，请查看你的角色卡")
    announce(room, "夜1", "第 1 个夜晚降临，请有夜晚行动的玩家在自己的屏幕上行动")
    store.update(room)
    return prep_night(room)


# ---------------- 夜晚 ----------------

def prep_night(room):
    """自动收集说书人代选的步骤，然后若无剩余玩家步骤则直接结算。"""
    rnd = random.Random()
    for s in room["night_state"]["steps"]:
        if s["collected"] is not None or s["id"] not in AUTO_STEPS:
            continue
        opts = [o["seat"] for o in s["pick"]["options"]]
        if s["pick"]["count"] == 0:
            s["collected"] = {"no_action": False, "targets": []}
        elif opts:
            s["collected"] = {"no_action": False,
                              "targets": rnd.sample(opts, s["pick"]["count"])}
        else:
            s["collected"] = {"no_action": True, "targets": []}
    store.update(room)
    return maybe_resolve(room)


def night_action(room, name, step_id, no_action=False, targets=None, cure=None):
    if room.get("phase") != "night":
        raise ValueError("当前不在夜晚")
    if room.get("night_pending"):
        raise ValueError("夜晚正在结算，无法更改行动")
    p = seat_of(room, name)
    step = next((s for s in room["night_state"]["steps"] if s["id"] == step_id), None)
    if step is None or step["seat"] != p["seat"] or step["id"] in AUTO_STEPS:
        raise ValueError("这不是你的行动步骤")
    if no_action:
        if not step["optional"]:
            raise ValueError("该行动不可跳过")
        collected = {"no_action": True, "targets": []}
    else:
        opts = {o["seat"] for o in step["pick"]["options"]}
        ts = [int(t) for t in (targets or [])]
        if len(ts) != step["pick"]["count"]:
            raise ValueError(f"需选择 {step['pick']['count']} 名目标")
        if len(set(ts)) != len(ts):
            raise ValueError("目标不可重复")
        if any(t not in opts for t in ts):
            raise ValueError("存在非法目标")
        if step["id"] == "protect" and ts[0] == p["flags"].get("last_protect"):
            raise ValueError("不可连续两晚守护同一人")
        if step["id"] == "jinzu" and ts[0] == p["flags"].get("last_jinzu"):
            raise ValueError("不可连续两晚禁足同一人")
        collected = {"no_action": False, "targets": ts}
        if step["extras"].get("cure_toggle"):
            collected["cure"] = True if cure is None else bool(cure)
    engine.record_action(room, step_id, collected)
    store.update(room)
    return maybe_resolve(room)


def night_decision(room, name, value):
    pend = room.get("night_pending")
    p = seat_of(room, name)
    if not pend or pend["seat"] != p["seat"]:
        raise ValueError("当前没有需要你决定的事项")
    match = next((o for o in pend["options"] if str(o["value"]) == str(value)), None)
    if match is None:
        raise ValueError("非法选项")
    room["night_answers"][pend["key"]] = match["value"]
    room["night_pending"] = None
    store.update(room)
    return _resolve_loop(room)


def maybe_resolve(room):
    ns = room.get("night_state")
    if room.get("phase") != "night" or not ns or room.get("night_pending"):
        return room
    if any(s["collected"] is None for s in ns["steps"]):
        return room
    return _resolve_loop(room)


def _player_decision_seat(room, key):
    """需要交给具体玩家（而非随机）的结算决定。"""
    if key.startswith("nian_"):
        return int(key.split("_", 1)[1])
    if key == "tsh_successor":
        for p in room["seats"]:
            if p.get("demon_kind") == "taishanghuang":
                return p["seat"]
    return None


def _resolve_loop(room):
    answers = room.setdefault("night_answers", {})
    rnd = random.Random()
    while True:
        result, _ = engine.resolve_night(room, answers, commit=False)
        pend = result.get("pending")
        if pend:
            seat = _player_decision_seat(room, pend["key"])
            if seat is not None:
                room["night_pending"] = {**pend, "seat": seat}
                store.update(room)
                return room
            answers[pend["key"]] = rnd.choice(pend["options"])["value"]
            continue
        result, new_game = engine.resolve_night(room, answers, commit=True)
        return _after_night(room, new_game, result["report"])


def _after_night(old, g, rpt):
    n = g["night_number"]
    tag = f"夜{n}"
    g["night_answers"] = {}
    g["night_pending"] = None
    if rpt["deaths"]:
        names = "、".join(f"{d['seat']}号 {d['name']}" for d in rpt["deaths"])
        announce(g, tag, f"黎明：昨夜死亡 —— {names}")
    else:
        announce(g, tag, "黎明：昨夜平安无事")
    for r in rpt["revived"]:
        announce(g, tag, f"叶澜依的祝福显灵：{r['seat']}号 {r['name']} 复活了！")
    for note in rpt["notes"]:
        g["hidden_log"].append(f"{tag}：{note}")
    for pkt in rpt["packets"]:
        lines = _fake_lines(g, pkt) if pkt.get("malfunction") else pkt["lines"]
        private(g, pkt["seat"], tag, lines)
    _transition_packets(old, g, tag)
    if g.get("phase") == "ended":
        _announce_end(g)
    else:
        d = g["day_number"]
        announce(g, f"天{d}",
                 f"第 {d} 个白天开始：存活 {engine.alive_count(g)} 人，"
                 f"处决门槛 {engine.vote_threshold(g)} 票")
        g["vote_state"] = None
        g["end_day_votes"] = []
    store.update(g)
    return g


def _transition_packets(old, new, tag):
    """对比夜晚前后各座位状态，把身份变化私信给当事人。"""
    demon = next((q for q in new["seats"] if q.get("is_demon") and q["alive"]), None)
    for po, pn in zip(old["seats"], new["seats"]):
        seat = pn["seat"]
        if pn.get("is_favored") and not po.get("is_favored"):
            lines = ["皇上雨露均沾：你已被转化为宠妃！",
                     "你加入邪恶阵营，失去原角色技能；新技能：每晚可禁足一名玩家"
                     "（其当晚行动无效，不可连续两晚同一人）。"]
            if demon:
                lines.append(f"皇上是 {engine.pub_label(demon)}。")
            private(new, seat, tag, lines)
        if po.get("is_favored") and not pn.get("is_favored") and pn["alive"]:
            private(new, seat, tag,
                    ["三阿哥染指了你：你脱离宠妃身份，恢复原角色、原阵营与技能。"])
        if pn.get("is_demon") and not po.get("is_demon") and pn["alive"]:
            kind = pn.get("demon_kind")
            if kind == "nvhuang":
                lines = ["皇上对你痛下杀手，你反杀成功！你现在是女皇：邪恶阵营新领袖，"
                         "每晚杀一名玩家。皇后技能已永久失效。"]
                team = [f"{engine.pub_label(q)}（{ROLE_BY_ID[q['role']]['name']}）"
                        for q in new["seats"]
                        if q["alignment"] == "evil" and q["seat"] != seat]
                if team:
                    lines.append("你的邪恶同伙：" + "、".join(team))
            elif kind == "huangshang":
                lines = ["太上皇传位于你：你现在是皇上（完整皇上技能，邪恶阵营）。"]
            elif kind == "taihou":
                lines = ["恶魔已死：你（皇后）秘密继任为新太后 —— 每晚选两名玩家，"
                         "死几人由天意自动裁定。"]
            else:
                lines = ["恶魔已死：你（皇后）秘密继任为新恶魔 —— 每晚杀一名玩家。"]
            private(new, seat, tag, lines)


def _fake_lines(g, pkt):
    """醉/毒玩家的信息包：机器人代替说书人编造假信息。"""
    rnd = random.Random()
    kind = pkt.get("kind")
    ctx = pkt.get("ctx") or {}
    n = len(g["seats"])

    def L(seat):
        return engine.pub_label(engine.get_p(g, seat))

    if kind == "huanbi_dist":
        return [f"恶魔与最近爪牙的间距：{rnd.randint(1, max(1, n // 2))}"]
    if kind == "jingfei_count":
        lo, hi = max(1, n // 2 - 2), min(n, n // 2 + 3)
        return [f"场上非男性角色数量：{rnd.randint(lo, hi)}（含苏培盛/温实初口径）"]
    if kind == "jingfei_death":
        return [f"敬妃死前得知：场上还有 {rnd.randint(1, max(2, n // 3))} 名邪恶玩家存活"]
    if kind == "xiaoyunzi" and ctx:
        same = rnd.random() < 0.5
        return [f"左邻 {L(ctx['left'])} 与右邻 {L(ctx['right'])}："
                f"{'同一阵营' if same else '不同阵营'}"]
    if kind == "jinxi" and ctx:
        has = rnd.random() < 0.5
        return [f"{L(ctx['t1'])} 与 {L(ctx['t2'])} 中{'有' if has else '没有'}恶魔"]
    if kind == "check" and ctx:
        yes = rnd.random() < 0.3
        return [f"{L(ctx['target'])} {'处于' if yes else '不处于'}中毒/醉酒状态"]
    if kind == "sanage":
        ok = rnd.random() < 0.5
        tgt = f"（目标 {L(ctx['target'])}）" if ctx.get("target") else ""
        return [f"手势：{'成功' if ok else '失败'}{tgt}"]
    if kind == "alr_info":
        females = [p for p in g["seats"]
                   if ROLE_BY_ID[p["role"]]["gender"] == "F" and p["seat"] != pkt["seat"]]
        froles = [r for r in ROLES if r["gender"] == "F" and not r.get("virtual")]
        if females:
            t = rnd.choice(females)
            return [f"告知安陵容：{engine.pub_label(t)} 是 {rnd.choice(froles)['name']}"]
    if kind == "yurao_bind" and ctx.get("target"):
        goods = [r for r in ROLES
                 if r["team"] in ("townsfolk", "outsider") and not r.get("virtual")]
        return [f"告知玉娆：{L(ctx['target'])} 是 {rnd.choice(goods)['name']}（羁绊绑定）"]
    return pkt["lines"]


# ---------------- 白天 ----------------

def player_day_actions(room, p):
    if room.get("phase") != "day":
        return []
    menus = engine.day_menus(room)
    acts = []
    for a in menus.get(p["seat"], []):
        if a["id"] in ALLOWED_DAY:
            acts.append(a)
        elif a["id"] == "direct_execute" and p["role"] == "kuangtu":
            acts.append({**a, "label": "狂徒：身份暴露 / 被捉奸 → 伏诛"})
    return acts


def day_action(room, name, action, params):
    if room.get("phase") != "day":
        raise ValueError("当前不在白天")
    p = seat_of(room, name)
    allowed = {a["id"] for a in player_day_actions(room, p)}
    if action not in allowed:
        raise ValueError("你当前不能执行该行动")
    params = params or {}
    if action in ("force_drunk", "silence", "accuse"):
        t = params.get("target")
        if not t:
            raise ValueError("请选择目标")
        # 死者同样可以作为目标（祺贵人检举死者身份、齐妃禁言死者均有意义）：
        # 不拦截，效果由 engine 判定
        if int(t) == p["seat"]:
            raise ValueError("不能以自己为目标")
    if action == "accuse":
        guess = params.get("role_guess")
        if guess not in ROLE_BY_ID:
            raise ValueError("请选择检举的角色")
    result = engine.day_action(room, p["seat"], action, params)
    tag = f"天{room['day_number']}"
    for ev in result["events"]:
        announce(room, tag, ev)
    return _after_day_change(room)


def nominate(room, name, nominee):
    if room.get("phase") != "day":
        raise ValueError("当前不在白天")
    p = seat_of(room, name)
    result = engine.nominate(room, p["seat"], int(nominee))
    tag = f"天{room['day_number']}"
    for ev in result["events"]:
        announce(room, tag, ev)
    if result.get("proceed_to_vote"):
        eligible = [q["seat"] for q in room["seats"] if q["alive"] or q["ghost_vote"]]
        room["vote_state"] = {"votes": {}, "eligible": eligible}
        announce(room, tag,
                 f"开始举手表决：门槛 {result['threshold']} 票，请全体有票玩家表态"
                 "（票型保密，全员表态后一并公开）")
    return _after_day_change(room)


def cast_vote(room, name, val):
    ds = room.get("day_state")
    vs = room.get("vote_state")
    if room.get("phase") != "day" or not ds or not ds.get("open_nomination") or not vs:
        raise ValueError("当前没有进行中的投票")
    p = seat_of(room, name)
    if p["seat"] not in vs["eligible"]:
        raise ValueError("你没有投票资格（幽灵票已用完）")
    vs["votes"][str(p["seat"])] = bool(val)
    if len(vs["votes"]) >= len(vs["eligible"]):
        yes = [int(s) for s, v in vs["votes"].items() if v]
        result = engine.vote(room, yes)
        tag = f"天{room['day_number']}"
        no = [int(s) for s, v in vs["votes"].items() if not v]
        names = "、".join(engine.pub_label(engine.get_p(room, s)) for s in sorted(yes))
        no_names = "、".join(engine.pub_label(engine.get_p(room, s)) for s in sorted(no))
        announce(room, tag,
                 f"举手表决结束（{len(yes)}/{len(vs['votes'])} 票赞成）"
                 f"，赞成：{names or '无人'}；反对：{no_names or '无人'}")
        for ev in result["events"]:
            announce(room, tag, ev)
        room["vote_state"] = None
        return _after_day_change(room)
    store.update(room)
    return room


def toggle_end_day(room, name):
    ds = room.get("day_state")
    if room.get("phase") != "day" or not ds:
        raise ValueError("当前不在白天")
    if ds.get("open_nomination"):
        raise ValueError("投票进行中，暂时不能入夜")
    p = seat_of(room, name)
    if not p["alive"]:
        raise ValueError("死亡玩家无需表态")
    votes = room.setdefault("end_day_votes", [])
    if p["seat"] in votes:
        votes.remove(p["seat"])
    else:
        votes.append(p["seat"])
    valid = [s for s in votes if engine.get_p(room, s)["alive"]]
    if len(valid) >= engine.alive_count(room) // 2 + 1:
        announce(room, f"天{room['day_number']}", "过半玩家同意收灯，白天结束")
        return _to_night(room)
    store.update(room)
    return room


def _after_day_change(room):
    if room.get("phase") == "ended":
        _announce_end(room)
        store.update(room)
        return room
    ds = room.get("day_state")
    if room.get("phase") == "day" and ds and ds.get("ended"):
        return _to_night(room)
    store.update(room)
    return room


def _to_night(room):
    engine.end_day(room)
    room["vote_state"] = None
    room["end_day_votes"] = []
    announce(room, f"夜{room['night_number']}",
             f"第 {room['night_number']} 个夜晚降临，请有夜晚行动的玩家在自己的屏幕上行动")
    store.update(room)
    return prep_night(room)


def _announce_end(room):
    if room.get("_end_announced"):
        return
    room["_end_announced"] = True
    side = "善良阵营" if room.get("winner") == "good" else "邪恶阵营"
    announce(room, "终局", f"游戏结束：{side}获胜！{room.get('win_reason') or ''}")


# ---------------- 视图 ----------------

def view(room, name=None):
    phase = room.get("phase")
    ended = phase == "ended"
    me = None
    if name:
        me = next((p for p in room["seats"] if p.get("name") == name), None)

    out = {
        "id": room["id"],
        "phase": phase,
        "player_count": room["player_count"],
        "night_number": room.get("night_number"),
        "day_number": room.get("day_number"),
        "setup_name": room.get("setup_name"),
        "winner": room.get("winner"),
        "win_reason": room.get("win_reason"),
        "announcements": room.get("announcements", []),
        "my_seat": me["seat"] if me else None,
        "seats": [],
    }

    for p in room["seats"]:
        s = {"seat": p["seat"], "name": p.get("name") or ""}
        if phase == "lobby":
            s["ready"] = bool(room["ready"].get(str(p["seat"])))
        else:
            s["alive"] = p.get("alive")
            s["ghost_vote"] = p.get("ghost_vote")
        if ended:
            role = ROLE_BY_ID.get(p.get("role") or "")
            orig = ROLE_BY_ID.get(p.get("original_role") or "")
            s["role"] = p.get("role")
            s["role_name"] = role["name"] if role else None
            s["team"] = role["team"] if role else None
            if orig and orig is not role:
                s["original_role_name"] = orig["name"]
            s["alignment"] = p.get("alignment")
        out["seats"].append(s)

    if phase not in ("lobby", None, "prepare"):
        out["alive_count"] = engine.alive_count(room)
        out["threshold"] = engine.vote_threshold(room)

    if ended:
        out["hidden_log"] = room.get("hidden_log", [])

    ds = room.get("day_state")
    if phase == "day" and ds:
        out["day"] = {
            "nominators": ds["nominators"],
            "nominees": ds["nominees"],
            "silenced": ds.get("silenced"),
            "ended": ds.get("ended"),
            "end_day_votes": room.get("end_day_votes", []),
            "end_day_need": engine.alive_count(room) // 2 + 1,
            "open_vote": None,
        }
        nom = ds.get("open_nomination")
        vs = room.get("vote_state")
        if nom and vs:
            # 票型保密：只公开「已表态人数」，不下发任何人的票向（自己的除外）
            ov = {
                "nominator": nom["nominator"],
                "nominee": nom["nominee"],
                "eligible": vs["eligible"],
                "pending": [s for s in vs["eligible"] if str(s) not in vs["votes"]],
                "voted": len(vs["votes"]),
                "total": len(vs["eligible"]),
                "threshold": engine.vote_threshold(room),
            }
            if me:
                ov["my_vote"] = vs["votes"].get(str(me["seat"]))
            out["day"]["open_vote"] = ov

    if phase == "night" and room.get("night_state"):
        steps = room["night_state"]["steps"]
        waiting = sorted({s["seat"] for s in steps if s["collected"] is None})
        out["night"] = {
            "number": room["night_state"]["number"],
            "waiting_count": len(waiting),
            "resolving": bool(room.get("night_pending")),
        }
        if me:
            mine = [s for s in steps
                    if s["seat"] == me["seat"] and s["id"] not in AUTO_STEPS]
            out["night"]["my_steps"] = [{
                "id": s["id"], "title": s["title"], "prompt": s["prompt"],
                "count": s["pick"]["count"], "options": s["pick"]["options"],
                "optional": s["optional"], "extras": s["extras"],
                "done": s["collected"] is not None,
                "collected": s["collected"],
            } for s in mine]
            pend = room.get("night_pending")
            if pend and pend["seat"] == me["seat"]:
                out["night"]["my_decision"] = {
                    "prompt": pend["prompt"], "options": pend["options"]}

    if me and phase not in ("lobby", None, "prepare"):
        role = ROLE_BY_ID[me["role"]]
        my = {
            "seat": me["seat"], "name": me["name"],
            "role": me["role"], "role_name": role["name"], "role_title": role["title"],
            "team": role["team"], "team_name": TEAM_NAMES[role["team"]],
            "ability": role["ability"],
            "alignment": me["alignment"], "alive": me["alive"],
            "ghost_vote": me["ghost_vote"],
            "is_favored": me.get("is_favored"),
            "is_demon": me.get("is_demon"),
            "notes": [],
        }
        if me.get("flags", {}).get("duishi"):
            my["notes"].append("苏培盛与你对食：你开局即为爪牙（邪恶阵营），照常收到信息但为邪恶效力")
        if me.get("is_favored"):
            my["notes"].append("你已被转化为宠妃：邪恶阵营，失去原技能；每晚可禁足一名玩家")
        if me.get("is_demon") and me["role"] == "huanghou":
            my["notes"].append("你已继任恶魔：每晚行使夜杀")
        if me["alignment"] == "evil":
            my["evil_team"] = [
                {"seat": q["seat"], "name": q["name"],
                 "role_name": "宠妃" if q.get("is_favored") else ROLE_BY_ID[q["role"]]["name"],
                 "demon": bool(q.get("is_demon")), "alive": q["alive"]}
                for q in room["seats"] if q.get("alignment") == "evil"]
        if phase == "day" and ds:
            my["day_actions"] = player_day_actions(room, me)
            my["can_nominate"] = (
                me["alive"] and not ds.get("ended") and not ds.get("open_nomination")
                and me["seat"] not in ds["nominators"])
            my["chatted"] = me["seat"] in ds.get("private_chats", [])
        my["private_log"] = room.get("private_log", {}).get(str(me["seat"]), [])
        out["me"] = my

    return out
