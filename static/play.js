/* 血战甄嬛传 —— 无说书人模式玩家端 */

const app = document.getElementById("app");
const LS_KEY = "zq_play";

const state = {
  meta: null,          // {roles, team_names, min_players, max_players}
  room: null,          // 房间号
  name: null,          // 我的玩家名
  view: null,          // 最新视图
  lastRaw: "",         // 变更检测
  sel: {},             // 夜晚步骤选择缓存 {key: {targets:[], cure:true}}
  selNight: null,      // sel 对应的夜数
  ui: { joinName: "", openAction: null, actTarget: null, actGuess: "", nomTarget: null },
  timer: null,
};

// ---------- 工具 ----------

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!res.ok) {
    let msg = `请求失败 (${res.status})`;
    try { msg = (await res.json()).detail || msg; } catch {}
    const err = new Error(msg);
    err.status = res.status;
    throw err;
  }
  return res.json();
}

function toast(msg) {
  const el = document.createElement("div");
  el.className = "toast";
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 3000);
}

function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  if (typeof attrs === "string" || attrs?.nodeType) {
    children.unshift(attrs);
    attrs = {};
  }
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c == null) continue;
    el.append(c.nodeType ? c : document.createTextNode(c));
  }
  return el;
}

function roleAvatar(roleId, cls = "sm") {
  if (!roleId) return null;
  return h("img", {
    class: `avatar ${cls}`,
    src: `/img/roles/${roleId}.jpg`,
    alt: "",
    onerror: (e) => e.target.remove(),
  });
}

function saveLS() {
  localStorage.setItem(LS_KEY, JSON.stringify({ room: state.room, name: state.name }));
}

function leaveRoom() {
  state.room = null;
  state.name = null;
  state.view = null;
  state.lastRaw = "";
  state.sel = {};
  saveLS();
  if (state.timer) { clearInterval(state.timer); state.timer = null; }
  renderEntry();
}

function seatName(v, seat) {
  const s = v.seats.find((x) => x.seat === seat);
  return s ? `${seat}号 ${s.name}` : `${seat}号`;
}

// ---------- 轮询 ----------

async function poll(force = false) {
  if (!state.room) return;
  let v;
  try {
    const q = state.name ? `?name=${encodeURIComponent(state.name)}` : "";
    v = await api(`/api/rooms/${state.room}/view${q}`);
  } catch (e) {
    if (e.status === 404) { toast("房间不存在或已被删除"); leaveRoom(); }
    return;
  }
  setView(v, force);
}

function setView(v, force = false) {
  const raw = JSON.stringify(v);
  if (!force && raw === state.lastRaw) return;
  state.lastRaw = raw;
  state.view = v;
  render();
}

function startPolling() {
  if (state.timer) clearInterval(state.timer);
  state.timer = setInterval(poll, 1500);
  poll(true);
}

// ---------- 入口 ----------

async function boot() {
  try { state.meta = await api("/api/meta"); } catch {}
  try {
    const saved = JSON.parse(localStorage.getItem(LS_KEY) || "{}");
    state.room = saved.room || null;
    state.name = saved.name || null;
  } catch {}
  if (state.room) startPolling();
  else renderEntry();
}

function renderEntry() {
  app.replaceChildren(
    h("div", { class: "play-wrap" },
      h("div", { class: "pcard" },
        h("h3", { class: "pcard-t" }, "加入房间"),
        h("div", { class: "join-row" },
          h("input", {
            class: "gid-input", placeholder: "输入 4 位房间号（如 4827）",
            id: "room-input",
            onkeydown: (e) => { if (e.key === "Enter") joinById(); },
          }),
          h("button", { class: "primary", onclick: joinById }, "进入")),
        h("p", { class: "hint", style: "margin:10px 0 0" },
          "开局后中途掉线？输入同一房间号，选择你的玩家名即可回到对局")),
      h("div", { class: "pcard" },
        h("h3", { class: "pcard-t" }, "创建房间"),
        h("p", { class: "hint", style: "text-align:left;margin-bottom:10px" },
          "只需选择人数；角色板子与身份在全员就绪后自动分配，机器人担任说书人"),
        h("div", { class: "count-grid", style: "margin:10px 0" },
          ...range(state.meta?.min_players ?? 7, state.meta?.max_players ?? 15).map((n) =>
            h("button", { class: "count-btn small-count", onclick: () => createRoom(n) }, String(n))))),
      h("p", { class: "hint" },
        h("a", { href: "/", style: "color:var(--gold-dim)" }, "← 返回说书人助手模式"))),
  );
}

function range(a, b) {
  return Array.from({ length: b - a + 1 }, (_, i) => a + i);
}

async function createRoom(n) {
  try {
    const res = await api("/api/rooms", { method: "POST", body: JSON.stringify({ player_count: n }) });
    state.room = res.id;
    state.name = null;
    saveLS();
    startPolling();
  } catch (e) { toast(e.message); }
}

function joinById() {
  const val = document.getElementById("room-input").value.trim();
  if (!val) return toast("请输入房间号");
  state.room = val;
  state.name = null;
  saveLS();
  startPolling();
}

// ---------- 渲染分发 ----------

function render() {
  const v = state.view;
  if (!v) return renderEntry();
  if (v.phase === "lobby") return renderLobby(v);
  if (!v.my_seat) return renderPickName(v);
  renderGame(v);
}

// ---------- 大厅 ----------

function renderLobby(v) {
  const mySeat = v.my_seat;
  const meReady = mySeat ? v.seats[mySeat - 1].ready : false;
  const claimed = v.seats.filter((s) => s.name).length;

  app.replaceChildren(
    h("div", { class: "play-wrap" },
      h("div", { class: "pcard", style: "text-align:center" },
        h("div", { class: "hint", style: "margin-bottom:4px" }, "房间号（告诉朋友们输入它加入）"),
        h("div", { class: "room-code", onclick: () => copyCode(v.id) }, v.id),
        h("div", { class: "hint" }, `${v.player_count} 人局 · 已入座 ${claimed}/${v.player_count} · 点击房间号复制`)),
      !mySeat ? h("div", { class: "pcard" },
        h("h3", { class: "pcard-t" }, "输入你的名字，然后点一个空座位入座"),
        h("input", {
          class: "gid-input", style: "width:100%", placeholder: "你的名字",
          value: state.ui.joinName,
          oninput: (e) => { state.ui.joinName = e.target.value; },
        })) : null,
      h("div", { class: "pcard" },
        h("h3", { class: "pcard-t" }, "座位"),
        h("div", { class: "lobby-seats" },
          ...v.seats.map((s) => {
            const mine = s.seat === mySeat;
            return h("div", { class: `lobby-seat ${mine ? "me" : ""} ${s.name ? "taken" : ""}` },
              h("span", { class: "seat-no" }, `${s.seat}号`),
              s.name
                ? h("span", { class: "lobby-name" }, s.name, s.ready ? " ✓" : "")
                : h("button", { class: "small", onclick: () => sitDown(s.seat) }, "入座"));
          })),
        mySeat ? h("div", { class: "actions" },
          h("button", {
            class: meReady ? "" : "primary",
            onclick: () => setReady(!meReady),
          }, meReady ? "取消就绪" : "我已就绪"),
          h("span", { class: "hint", style: "margin:0" },
            "全员入座并就绪后自动发身份开局")) : null),
      h("div", { class: "actions" },
        h("button", { class: "small", onclick: leaveRoom }, "退出房间"))),
  );
}

function copyCode(id) {
  navigator.clipboard?.writeText(id).then(() => toast("房间号已复制"));
}

async function sitDown(seat) {
  const name = state.name || state.ui.joinName.trim();
  if (!name) return toast("请先输入你的名字");
  try {
    const v = await api(`/api/rooms/${state.room}/join`, {
      method: "POST", body: JSON.stringify({ name, seat }),
    });
    state.name = name;
    saveLS();
    setView(v, true);
  } catch (e) { toast(e.message); }
}

async function setReady(ready) {
  try {
    const v = await api(`/api/rooms/${state.room}/ready`, {
      method: "POST", body: JSON.stringify({ name: state.name, ready }),
    });
    setView(v, true);
  } catch (e) { toast(e.message); }
}

// ---------- 重连选名 ----------

function renderPickName(v) {
  app.replaceChildren(
    h("div", { class: "play-wrap" },
      h("div", { class: "pcard" },
        h("h3", { class: "pcard-t" }, `房间 ${v.id} 对局进行中 —— 你是哪位玩家？`),
        h("div", { class: "chips", style: "margin-top:10px" },
          ...v.seats.filter((s) => s.name).map((s) =>
            h("button", {
              class: "chip",
              onclick: () => { state.name = s.name; saveLS(); poll(true); },
            }, `${s.seat}号 ${s.name}`)))),
      h("div", { class: "actions" },
        h("button", { class: "small", onclick: leaveRoom }, "退出房间"))),
  );
}

// ---------- 对局主界面 ----------

function renderGame(v) {
  if (v.night_number !== state.selNight) {
    state.sel = {};
    state.selNight = v.night_number;
  }
  const wrap = h("div", { class: "play-wrap" });
  wrap.append(gameHeader(v));
  if (v.phase === "ended") wrap.append(endBanner(v));
  wrap.append(roleCard(v));
  if (v.phase === "night") wrap.append(nightPanel(v));
  if (v.phase === "day") wrap.append(dayPanel(v));
  wrap.append(seatsPanel(v));
  const priv = privateFeed(v);
  if (priv) wrap.append(priv);
  wrap.append(announceFeed(v));
  if (v.phase === "ended") wrap.append(revealPanel(v));
  wrap.append(h("div", { class: "actions" },
    h("button", { class: "small", onclick: leaveRoom }, "退出房间")));
  app.replaceChildren(wrap);
}

function gameHeader(v) {
  let phase, cls;
  if (v.phase === "night") { phase = `第 ${v.night_number} 夜`; cls = "night"; }
  else if (v.phase === "day") { phase = `第 ${v.day_number} 天`; cls = "day"; }
  else { phase = "终局"; cls = "ended"; }
  const dead = v.seats.filter((s) => s.alive === false);
  const ghosts = dead.filter((s) => s.ghost_vote).length;
  return h("div", { class: "pcard play-bar" },
    h("span", { class: `session-phase ${cls}` }, phase),
    h("span", { class: "bar-info" },
      `存活 ${v.alive_count}/${v.player_count} · 处决门槛 ${v.threshold} 票`,
      dead.length ? ` · 幽灵票 ${ghosts}/${dead.length}` : ""),
    h("span", { class: "session-id" }, v.id));
}

function roleCard(v) {
  const me = v.me;
  if (!me) return h("div");
  const card = h("div", { class: `pcard role-card team-${me.team}` },
    h("div", { class: "detail-head" },
      roleAvatar(me.role, "lg"),
      h("div", {},
        h("div", { class: "role-line" },
          h("span", { class: "role-big" }, me.role_name),
          h("span", { class: `comp-chip ${me.team}` }, me.team_name),
          me.alignment === "evil" ? h("span", { class: "badge b-demon" }, "邪恶阵营") : null,
          !me.alive ? h("span", { class: "badge b-ghost" }, me.ghost_vote ? "已死·有幽灵票" : "已死·无票") : null),
        h("div", { class: "hint", style: "text-align:left;margin:2px 0 0" },
          `${me.seat}号 ${me.name} · ${me.role_title}`))),
    h("div", { class: "role-ability" }, me.ability),
    ...(me.notes || []).map((n) => h("div", { class: "warning info" }, n)));
  if (me.evil_team) {
    card.append(h("div", { class: "evil-box" },
      h("div", { class: "evil-t" }, "邪恶阵营互认"),
      ...me.evil_team.map((q) =>
        h("div", { class: "evil-row" },
          `${q.seat}号 ${q.name} —— ${q.role_name}`,
          q.demon ? "（恶魔）" : "",
          q.alive === false ? "（已死）" : ""))));
  }
  return card;
}

// ---------- 夜晚 ----------

function nightPanel(v) {
  const night = v.night || {};
  const panel = h("div", { class: "pcard" },
    h("h3", { class: "pcard-t" }, `第 ${night.number} 夜 · 行动`));

  if (night.my_decision) {
    panel.append(
      h("div", { class: "warning info" }, night.my_decision.prompt),
      h("div", { class: "chips" },
        ...night.my_decision.options.map((o) =>
          h("button", { class: "chip", onclick: () => sendDecision(o.value) }, o.label))));
    return panel;
  }

  const steps = night.my_steps || [];
  if (!steps.length) {
    panel.append(h("p", { class: "night-wait" },
      night.resolving ? "夜晚结算中，等待某位玩家做出决定…"
        : `你今晚无需行动，安心闭眼。等待其他玩家…（还剩 ${night.waiting_count} 步未完成）`));
    return panel;
  }
  for (const s of steps) panel.append(stepForm(v, s));
  if (night.waiting_count > 0) {
    panel.append(h("p", { class: "hint", style: "text-align:left" },
      night.resolving ? "夜晚结算中…" : `全场还剩 ${night.waiting_count} 步未完成`));
  }
  return panel;
}

function stepForm(v, s) {
  const key = s.id;
  const sel = state.sel[key] ||= { targets: [], cure: true };
  const box = h("div", { class: `step-box ${s.done ? "done" : ""}` },
    h("div", { class: "step-title" }, s.title, s.done ? " ✓ 已提交（可修改）" : ""),
    h("div", { class: "night-prompt" }, s.prompt));

  if (s.count > 0) {
    box.append(h("div", { class: "chips" },
      ...s.options.map((o) => {
        const on = sel.targets.includes(o.seat);
        return h("button", {
          class: `chip ${on ? "on" : ""}`,
          onclick: () => {
            if (on) sel.targets = sel.targets.filter((t) => t !== o.seat);
            else {
              sel.targets.push(o.seat);
              if (sel.targets.length > s.count) sel.targets.shift();
            }
            render();
          },
        }, o.label, o.note ? h("span", { class: "chip-note" }, ` ${o.note}`) : null);
      })));
  }
  if (s.extras?.cure_toggle) {
    box.append(h("label", { class: "cure-box" },
      h("input", {
        type: "checkbox", checked: sel.cure,
        onchange: (e) => { sel.cure = e.target.checked; },
      }),
      " 若目标处于中毒/醉酒，为其解除"));
  }
  const btns = h("div", { class: "actions", style: "justify-content:flex-start;margin-top:8px" });
  btns.append(h("button", {
    class: "primary",
    disabled: sel.targets.length !== s.count,
    onclick: () => sendNightAction(s, false),
  }, s.count > 0 ? `确认（已选 ${sel.targets.length}/${s.count}）` : "确认"));
  if (s.optional) {
    btns.append(h("button", { onclick: () => sendNightAction(s, true) }, "无行动"));
  }
  box.append(btns);
  return box;
}

async function sendNightAction(s, noAction) {
  const sel = state.sel[s.id] || { targets: [], cure: true };
  try {
    const v = await api(`/api/rooms/${state.room}/night/action`, {
      method: "POST",
      body: JSON.stringify({
        name: state.name, step_id: s.id,
        no_action: noAction, targets: noAction ? [] : sel.targets,
        cure: sel.cure,
      }),
    });
    setView(v, true);
  } catch (e) { toast(e.message); }
}

async function sendDecision(value) {
  try {
    const v = await api(`/api/rooms/${state.room}/night/decision`, {
      method: "POST", body: JSON.stringify({ name: state.name, value }),
    });
    setView(v, true);
  } catch (e) { toast(e.message); }
}

// ---------- 白天 ----------

function dayPanel(v) {
  const d = v.day || {};
  const me = v.me || {};
  const panel = h("div", { class: "pcard" },
    h("h3", { class: "pcard-t" }, `第 ${v.day_number} 天 · 讨论与投票`));

  if (d.silenced) {
    panel.append(h("div", { class: "warning" },
      `${seatName(v, d.silenced)} 已被齐妃禁言：今天不可说话，违规将被直接处决`));
  }

  if (d.open_vote) {
    panel.append(votePanel(v, d.open_vote));
    return panel;
  }

  if (d.ended) {
    panel.append(h("p", { class: "night-wait" }, "今日已处决，即将入夜…"));
    return panel;
  }

  // 提名
  if (me.can_nominate) {
    const cands = v.seats.filter((s) => s.alive && !d.nominees.includes(s.seat));
    panel.append(h("div", { class: "step-box" },
      h("div", { class: "step-title" }, "发起提名（每人每天一次；被提名过的人不可再被提名）"),
      h("div", { class: "chips" },
        ...cands.map((s) => h("button", {
          class: `chip ${state.ui.nomTarget === s.seat ? "on" : ""}`,
          onclick: () => { state.ui.nomTarget = state.ui.nomTarget === s.seat ? null : s.seat; render(); },
        }, `${s.seat}号 ${s.name}`))),
      h("div", { class: "actions", style: "justify-content:flex-start;margin-top:8px" },
        h("button", {
          class: "primary", disabled: state.ui.nomTarget == null,
          onclick: doNominate,
        }, "提名并发起投票"))));
  } else if (me.alive) {
    panel.append(h("p", { class: "hint", style: "text-align:left" },
      d.nominators.includes(me.seat) ? "你今天已经提名过了" : "当前无法提名"));
  }

  // 日间技能
  for (const a of me.day_actions || []) {
    panel.append(dayActionRow(v, a));
  }

  // 收灯（无处决入夜）
  const votes = d.end_day_votes || [];
  const meVoted = votes.includes(me.seat);
  if (me.alive) {
    panel.append(h("div", { class: "actions", style: "justify-content:flex-start;margin-top:10px" },
      h("button", {
        class: meVoted ? "on chip" : "",
        onclick: toggleEndDay,
      }, meVoted ? `已提议入夜（${votes.length}/${d.end_day_need}）点击撤回` : `提议入夜（${votes.length}/${d.end_day_need}）`),
      h("span", { class: "hint", style: "margin:0" }, "过半存活玩家提议后直接入夜")));
  }
  return panel;
}

function dayActionRow(v, a) {
  const open = state.ui.openAction === a.id;
  const row = h("div", { class: "step-box" },
    h("div", { class: "actions", style: "justify-content:flex-start" },
      h("button", {
        class: a.danger ? "danger" : "",
        onclick: () => {
          if (!a.needs_target && !a.needs_role_guess) return doDayAction(a.id, {});
          state.ui.openAction = open ? null : a.id;
          state.ui.actTarget = null;
          state.ui.actGuess = "";
          render();
        },
      }, a.label)));
  if (open && a.needs_target) {
    // 死者同样可选（如检举一名已死玩家的身份）：只标注，不拦截
    const cands = v.seats.filter((s) => s.seat !== v.my_seat);
    row.append(h("div", { class: "chips", style: "margin-top:8px" },
      ...cands.map((s) => h("button", {
        class: `chip ${state.ui.actTarget === s.seat ? "on" : ""}`,
        onclick: () => { state.ui.actTarget = s.seat; render(); },
      }, `${s.seat}号 ${s.name}`,
        s.alive === false ? h("span", { class: "chip-note" }, " 已死") : null))));
    if (a.needs_role_guess) {
      const sel = h("select", {
        onchange: (e) => { state.ui.actGuess = e.target.value; },
      }, h("option", { value: "" }, "——选择检举的角色——"));
      for (const r of state.meta?.roles || []) {
        if (r.virtual) continue;
        const opt = h("option", { value: r.id }, `${r.name}（${state.meta.team_names[r.team]}）`);
        if (state.ui.actGuess === r.id) opt.setAttribute("selected", "");
        sel.append(opt);
      }
      row.append(h("div", { class: "day-act" }, sel));
    }
    row.append(h("div", { class: "actions", style: "justify-content:flex-start;margin-top:8px" },
      h("button", {
        class: "primary",
        disabled: state.ui.actTarget == null || (a.needs_role_guess && !state.ui.actGuess),
        onclick: () => doDayAction(a.id, {
          target: state.ui.actTarget,
          ...(a.needs_role_guess ? { role_guess: state.ui.actGuess } : {}),
        }),
      }, "确认")));
  }
  return row;
}

function votePanel(v, ov) {
  const my = v.my_seat;
  const myVote = ov.my_vote;
  const eligible = ov.eligible.includes(my);
  const pending = ov.pending || [];
  const panel = h("div", { class: "step-box vote-box" },
    h("div", { class: "step-title" },
      `${seatName(v, ov.nominator)} 提名了 ${seatName(v, ov.nominee)}`),
    h("div", { class: "vote-tally" },
      h("span", { class: "vote-yes" }, `已表态 ${ov.voted}/${ov.total}`),
      ` · 门槛 ${ov.threshold} 票 · 票型保密，全员表态后一并公开`));
  if (pending.length) {
    panel.append(h("div", { class: "hands" },
      ...pending.map((seat) => {
        const s = v.seats.find((x) => x.seat === seat);
        return h("span", { class: "hand" },
          `${seat}号 ${s.name}`,
          s.alive === false ? "（幽灵票）" : "",
          " …");
      })));
    panel.append(h("p", { class: "hint", style: "text-align:left" },
      `等待以上 ${pending.length} 人表态`));
  }
  if (eligible) {
    panel.append(h("div", { class: "actions", style: "margin-top:10px" },
      h("button", {
        class: myVote === true ? "primary" : "",
        onclick: () => doVote(true),
      }, "✋ 赞成处决"),
      h("button", {
        class: myVote === false ? "primary" : "",
        onclick: () => doVote(false),
      }, "✊ 反对"),
      h("span", { class: "hint", style: "margin:0" },
        myVote == null
          ? "你的票只有你自己看得到；全员表态后自动公开并结算"
          : `你已投「${myVote ? "赞成" : "反对"}」，公开前可随时改票`)));
  } else {
    panel.append(h("p", { class: "hint" }, "你没有投票资格（幽灵票已用完）"));
  }
  return panel;
}

async function doNominate() {
  try {
    const v = await api(`/api/rooms/${state.room}/day/nominate`, {
      method: "POST", body: JSON.stringify({ name: state.name, nominee: state.ui.nomTarget }),
    });
    state.ui.nomTarget = null;
    setView(v, true);
  } catch (e) { toast(e.message); }
}

async function doDayAction(action, params) {
  if ((action === "violation" || action === "direct_execute")
      && !confirm("确认执行？此操作会立即处决并无法撤销")) return;
  try {
    const v = await api(`/api/rooms/${state.room}/day/action`, {
      method: "POST", body: JSON.stringify({ name: state.name, action, params }),
    });
    state.ui.openAction = null;
    setView(v, true);
  } catch (e) { toast(e.message); }
}

async function doVote(vote) {
  try {
    const v = await api(`/api/rooms/${state.room}/day/vote`, {
      method: "POST", body: JSON.stringify({ name: state.name, vote }),
    });
    setView(v, true);
  } catch (e) { toast(e.message); }
}

async function toggleEndDay() {
  try {
    const v = await api(`/api/rooms/${state.room}/day/end`, {
      method: "POST", body: JSON.stringify({ name: state.name }),
    });
    setView(v, true);
  } catch (e) { toast(e.message); }
}

// ---------- 玩家列表 / 信息流 ----------

function seatsPanel(v) {
  return h("div", { class: "pcard" },
    h("h3", { class: "pcard-t" }, "玩家"),
    h("div", { class: "pseat-grid" },
      ...v.seats.map((s) => {
        const dead = s.alive === false;
        return h("div", { class: `pseat ${dead ? "dead" : ""} ${s.seat === v.my_seat ? "me" : ""}` },
          v.phase === "ended" ? roleAvatar(s.role, "xs") : null,
          h("span", { class: "pseat-no" }, `${s.seat}号`),
          h("span", { class: "pseat-name" }, s.name),
          dead ? h("span", { class: "badge b-ghost" }, s.ghost_vote ? "亡·有票" : "亡") : null);
      })));
}

function privateFeed(v) {
  const logs = v.me?.private_log || [];
  if (!logs.length) return null;
  return h("div", { class: "pcard" },
    h("h3", { class: "pcard-t" }, "私密信息（只有你能看到）"),
    h("div", { class: "feed" },
      ...logs.slice().reverse().map((l) =>
        h("div", { class: "feed-item private" },
          h("span", { class: "feed-tag" }, l.tag),
          h("div", {}, ...l.lines.map((x) => h("div", {}, x)))))));
}

function announceFeed(v) {
  return h("div", { class: "pcard" },
    h("h3", { class: "pcard-t" }, "公告"),
    h("div", { class: "feed" },
      ...v.announcements.slice().reverse().map((a) =>
        h("div", { class: "feed-item" },
          h("span", { class: "feed-tag" }, a.tag),
          h("div", {}, a.text)))));
}

// ---------- 终局 ----------

function endBanner(v) {
  const good = v.winner === "good";
  return h("div", { class: `end-banner ${good ? "good" : "evil"}`, style: "max-width:none" },
    h("div", { class: "end-title" }, good ? "善良阵营获胜" : "邪恶阵营获胜"),
    h("div", {}, v.win_reason || ""));
}

function revealPanel(v) {
  return h("div", { class: "pcard" },
    h("h3", { class: "pcard-t" }, "身份公开"),
    ...v.seats.map((s) =>
      h("div", { class: "reveal-row" },
        roleAvatar(s.role, "xs"),
        h("span", { class: `reveal-role team-${s.team}` }, s.role_name || "?"),
        h("span", {}, `${s.seat}号 ${s.name}`),
        s.original_role_name ? h("span", { class: "hint", style: "margin:0" }, `（原：${s.original_role_name}）`) : null,
        h("span", { class: `badge ${s.alignment === "evil" ? "b-demon" : "b-ghost"}` },
          s.alignment === "evil" ? "邪恶" : "善良"))),
    v.hidden_log?.length ? h("details", { style: "margin-top:10px" },
      h("summary", { style: "cursor:pointer;color:var(--gold-dim)" }, "复盘：机器人说书人的隐藏记录"),
      ...v.hidden_log.map((x) => h("div", { class: "log-item" }, x))) : null);
}

boot();
