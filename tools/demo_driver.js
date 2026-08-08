/* 录屏演示驱动 —— 生成 docs/demo.mp4 时用的脚本，不参与应用运行。
 *
 * 用法：
 *   1. 把本文件复制到 static/ 下：cp tools/demo_driver.js static/demo_driver.js
 *   2. 在 static/index.html（说书人助手）或 static/play.html（无说书人模式）
 *      的 </body> 前临时插入：
 *        <script>window.__demoSpeed = 1.1;</script>
 *        <script src="demo_driver.js"></script>
 *   3. 打开一个干净的 Chrome 窗口（窗口内只留这一个标签页），访问对应页面并录屏；
 *      带上 ?v=时间戳 避免浏览器缓存旧的 html。
 *   4. 脚本跑完会请求 /api/games/DEMO_FINISHED/state（404），
 *      在服务端日志里 grep DEMO_FINISHED 即可知道何时停止录制；
 *      页面上也会置 window.__demoDone / __demoSecs。
 *   5. 录完记得撤销第 2 步的 html 改动并删掉 static/demo_driver.js。
 *
 * 录制参考命令（3456×2234 视网膜屏、Chrome 窗口 bounds {30,33,1530,1033}）：
 *   IDX=$(ffmpeg -f avfoundation -list_devices true -i "" 2>&1 \
 *         | grep -i "Capture screen 0" | sed -E 's,.*\[([0-9]+)\].*,\1,')
 *   ffmpeg -f avfoundation -capture_cursor 0 -framerate 30 -i "$IDX:none" \
 *     -vf "crop=3000:2000:60:66" -c:v h264_videotoolbox -realtime 1 -b:v 30M \
 *     -pix_fmt yuv420p seg.mp4
 *   # 再裁掉浏览器工具栏（页面内容从第 174 行像素开始）并压制：
 *   #   crop=3000:1824:0:174,scale=1920:-2 → libx264 crf 21
 *
 * 注意：录制前把真实鼠标移出取景范围（否则画面里会多出一个不动的指针）。
 */
(function () {
  if (window.__demoBooted) return;
  window.__demoBooted = true;

  const SPEED = window.__demoSpeed || 1;      // >1 更快
  const ms = (x) => x / SPEED;

  // 原生 confirm 会阻塞脚本与录制，演示期间一律放行
  window.confirm = () => true;

  // 玩家端每次演示都从空房间开始（必须早于 play.js 的 boot 读取 localStorage）
  if (location.pathname.includes("play")) {
    try { localStorage.removeItem("zq_play"); } catch (e) {}
  }

  // ---------------- 覆盖层：光标 / 字幕 / 标题卡 ----------------

  const style = document.createElement("style");
  style.textContent = `
  #dmo-cursor{position:fixed;left:0;top:0;z-index:99999;pointer-events:none;
    will-change:transform;filter:drop-shadow(0 2px 5px rgba(0,0,0,.65));}
  #dmo-ring{position:fixed;left:0;top:0;z-index:99998;pointer-events:none;
    width:0;height:0;border-radius:50%;border:2px solid #d4a95a;opacity:0;}
  #dmo-cap{position:fixed;left:28px;bottom:26px;z-index:99997;pointer-events:none;
    max-width:640px;background:rgba(20,12,14,.86);border:1px solid #6b4f2a;
    border-left:3px solid #d4a95a;border-radius:8px;padding:11px 18px;
    color:#efe3d0;font:15px/1.5 "PingFang SC","Hiragino Sans GB",sans-serif;
    letter-spacing:.5px;box-shadow:0 8px 28px rgba(0,0,0,.5);
    opacity:0;transform:translateY(10px);transition:opacity .35s,transform .35s;}
  #dmo-cap.on{opacity:1;transform:translateY(0);}
  #dmo-cap b{color:#d4a95a;font-weight:600;}
  #dmo-title{position:fixed;inset:0;z-index:99996;pointer-events:none;
    background:#1a1214;display:flex;flex-direction:column;align-items:center;
    justify-content:center;gap:14px;opacity:0;transition:opacity .6s;}
  #dmo-title.on{opacity:1;}
  #dmo-title .t{color:#d4a95a;font:600 40px/1.4 "PingFang SC",sans-serif;letter-spacing:8px;}
  #dmo-title .s{color:#a89684;font:16px/1.6 "PingFang SC",sans-serif;letter-spacing:3px;}
  #dmo-title .r{width:120px;height:1px;background:#6b4f2a;}
  `;
  document.head.appendChild(style);

  const cursor = document.createElement("div");
  cursor.id = "dmo-cursor";
  cursor.innerHTML =
    '<svg width="26" height="30" viewBox="0 0 26 30">' +
    '<path d="M2 1 L2 22.5 L7.6 17.4 L11.4 26.5 L15.6 24.7 L11.8 15.9 L19.4 15.4 Z" ' +
    'fill="#fff" stroke="#1a1214" stroke-width="1.6" stroke-linejoin="round"/></svg>';
  const ring = document.createElement("div");
  ring.id = "dmo-ring";
  const cap = document.createElement("div");
  cap.id = "dmo-cap";
  const title = document.createElement("div");
  title.id = "dmo-title";
  title.innerHTML = '<div class="t"></div><div class="r"></div><div class="s"></div>';
  document.body.append(cursor, ring, cap, title);

  let cx = innerWidth * 0.5, cy = innerHeight * 0.78;
  const draw = () => { cursor.style.transform = `translate(${cx}px, ${cy}px)`; };
  draw();

  const sleep = (t) => new Promise((r) => setTimeout(r, ms(t)));
  const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);

  function glide(x, y, dur) {
    const x0 = cx, y0 = cy;
    const d = Math.hypot(x - x0, y - y0);
    dur = ms(dur != null ? dur : Math.min(560, 180 + d * 0.45));
    return new Promise((res) => {
      const t0 = performance.now();
      let done = false;
      const end = () => {
        if (done) return;
        done = true;
        cx = x; cy = y; draw(); res();
      };
      // 后台标签页 rAF 会被暂停，用定时器兜底，避免脚本卡死
      setTimeout(end, dur + 500);
      (function step(now) {
        if (done) return;
        const k = Math.min(1, (now - t0) / dur);
        const e = ease(k);
        cx = x0 + (x - x0) * e;
        cy = y0 + (y - y0) * e;
        draw();
        k < 1 ? requestAnimationFrame(step) : end();
      })(t0);
    });
  }

  function pulse() {
    ring.style.transition = "none";
    ring.style.width = ring.style.height = "0px";
    ring.style.opacity = "0.95";
    ring.style.transform = `translate(${cx}px, ${cy}px)`;
    requestAnimationFrame(() => {
      ring.style.transition = `all ${ms(420)}ms ease-out`;
      ring.style.width = ring.style.height = "46px";
      ring.style.opacity = "0";
      ring.style.transform = `translate(${cx - 23}px, ${cy - 23}px)`;
    });
  }

  function caption(html) {
    cap.classList.remove("on");
    setTimeout(() => { cap.innerHTML = html; cap.classList.add("on"); }, ms(180));
  }
  const capOff = () => cap.classList.remove("on");

  async function titleCard(t, s, hold) {
    title.querySelector(".t").textContent = t;
    title.querySelector(".s").textContent = s;
    title.classList.add("on");
    await sleep(hold || 2200);
    title.classList.remove("on");
    await sleep(700);
  }

  // ---------------- DOM 助手 ----------------

  const $$ = (sel, root) => [...(root || document).querySelectorAll(sel)];
  const txt = (e) => (e.textContent || "").replace(/\s+/g, " ").trim();

  async function waitFor(fn, tmo) {
    const t0 = performance.now();
    for (;;) {
      let v = null;
      try { v = fn(); } catch (e) { /* 渲染中途 */ }
      if (v) return v;
      if (performance.now() - t0 > (tmo || 15000)) throw new Error("waitFor 超时");
      await sleep(50);
    }
  }
  const find = (sel, t) => $$(sel).find((e) => txt(e).includes(t));
  const waitEl = (sel, t) => waitFor(() => {
    const e = t == null ? $$(sel)[0] : find(sel, t);
    return e && e.offsetParent !== null ? e : null;
  });

  async function hover(el, dur) {
    const r = el.getBoundingClientRect();
    if (r.top < 40 || r.bottom > innerHeight - 40) {
      el.scrollIntoView({ block: "center", behavior: "smooth" });
      await sleep(420);
    }
    const b = el.getBoundingClientRect();
    await glide(b.left + b.width / 2, b.top + b.height / 2, dur);
  }

  async function click(el, pause) {
    await hover(el);
    await sleep(110);
    pulse();
    await sleep(100);
    el.click();
    await sleep(pause == null ? 330 : pause);
  }
  const clickText = async (sel, t, pause) => click(await waitEl(sel, t), pause);

  async function type(input, text) {
    await click(input, 50);
    for (const ch of text) {
      input.value += ch;
      input.dispatchEvent(new Event("input", { bubbles: true }));
      await sleep(55);
    }
    await sleep(120);
  }

  const T0 = performance.now();
  function finish() {
    window.__demoDone = true;
    window.__demoSecs = Math.round((performance.now() - T0) / 1000);
    fetch("/api/games/DEMO_FINISHED/state").catch(() => {});
  }

  // ---------------- 段一：说书人助手 ----------------

  const NAMES = ["沐白", "青禾", "晚晴", "临舟", "小满", "如意", "砚书", "观棋"];

  const seatEls = () => $$(".round-table .seat");
  const chipFor = (seat) =>
    $$(".night-card .chip").find((c) => txt(c).startsWith(seat + "号"));

  function pickTargets(step, v) {
    const R = state.roleById;
    const alive = v.seats.filter((s) => s.alive);
    const demon = alive.find((s) => s.is_demon);
    const opts = step.pick.options.map((o) => o.seat);
    const has = (s) => opts.includes(s);
    const need = step.pick.count;
    const seatOf = (s) => v.seats[s - 1];

    if (need === 0) return [];

    switch (step.id) {
      case "yulu": {                       // 皇上侍寝：挑一名女性善良角色
        const t = opts.map(seatOf).find(
          (p) => R[p.role] && R[p.role].gender === "F" &&
                 p.alignment !== "evil" && p.role !== "zhenhuan");
        return t ? [t.seat] : [];          // 找不到就无行动
      }
      case "protect":
        return has(window.__demoKill) ? [window.__demoKill] : [opts[0]];
      case "kill":
        if (need === 2) return opts.slice(0, 2);
        return has(window.__demoKill) ? [window.__demoKill] : [opts[0]];
      case "jinxi": {                      // 槿汐打听：故意把恶魔圈进去
        const rest = opts.filter((s) => !demon || s !== demon.seat);
        return demon && has(demon.seat) ? [demon.seat, rest[0]] : opts.slice(0, 2);
      }
      case "jinzu": {                      // 宠妃禁足：挑个不影响剧情的人
        const t = opts.find((s) => seatOf(s).role !== "guojunwang" &&
                                   s !== window.__demoKill);
        return [t != null ? t : opts[0]];
      }
      case "revive":
        return [];                          // 无行动
      default:
        return opts.slice(0, need);
    }
  }

  async function runNight() {
    const v0 = state.view;
    // 预先定好今晚的“刀口”，让守护正好挡在上面
    const alive = v0.seats.filter((s) => s.alive);
    const victim =
      alive.find((s) => !s.is_demon && s.alignment !== "evil" &&
                        s.role !== "guojunwang" && s.role !== "zhenhuan" &&
                        state.roleById[s.role].team === "townsfolk") ||
      alive.find((s) => !s.is_demon);
    window.__demoKill = victim ? victim.seat : null;

    for (;;) {
      const ns = state.view.night_state;
      if (!ns) break;
      const step = ns.steps.find((s) => s.collected === null);
      if (!step) break;

      const item = await waitFor(() => $$(".step-item.current")[0]);
      await hover(item, 320);
      await sleep(260);

      const targets = pickTargets(step, state.view);
      if (!targets.length && step.optional && step.pick.count > 0) {
        await clickText(".night-card button", "无行动", 420);
        continue;
      }
      for (const t of targets) {
        const chip = await waitFor(() => chipFor(t));
        await click(chip, 200);
      }
      const btn = await waitFor(() =>
        $$(".night-card .actions button").find((b) => txt(b).startsWith("确认")));
      await click(btn, 420);
    }

    caption("全部行动收集完毕 —— <b>按规则自动结算</b>：死亡链 / 守护 / 反杀 / 继任 / 醉毒");
    await sleep(1400);
    await clickText(".night-card button", "开始结算", 900);

    // 需要说书人裁定时逐个选择
    for (;;) {
      const pend = find(".screen-title", "需要说书人裁定");
      if (!pend) break;
      caption("引擎遇到<b>需说书人裁定</b>的分支，弹出选项由你决定");
      await sleep(900);
      await click(await waitEl(".chips .chip"), 800);
    }

    await waitEl(".screen-title", "夜晚结算报告");
    caption("三栏报告：<b>黎明公布</b> · <b>逐位传信</b>（含醉/毒需给假信息的提示） · <b>说书人备忘</b>");
    await sleep(3400);
    const go = await waitFor(() =>
      $$(".actions button.primary").find((b) => txt(b).includes("确认")));
    await click(go, 900);
  }

  async function runDay(nomineeSeat, story) {
    await waitEl(".screen-title", "个白天");
    caption(story);
    await sleep(2200);

    await clickText(".side-panel button", "发起提名", 700);
    const modal = await waitEl(".modal");
    const sels = $$("select", modal);
    const nominator = state.view.seats.find(
      (s) => s.alive && s.seat !== nomineeSeat &&
             [...sels[0].options].some((o) => +o.value === s.seat));
    await hover(sels[0], 420); pulse(); await sleep(260);
    sels[0].value = String(nominator.seat);
    await sleep(500);
    await hover(sels[1], 420); pulse(); await sleep(260);
    sels[1].value = String(nomineeSeat);
    await sleep(700);
    await clickText(".modal .actions button", "提交提名", 800);

    // 投票弹窗：全场同时举手
    await waitEl(".modal h3", "处决投票");
    caption("全场<b>同时举手</b>：点选举手玩家，实时对照处决门槛（死亡玩家举手将消耗幽灵票）");
    await sleep(1800);
    const need = state.view.threshold;
    const voters = state.view.seats.filter((s) => s.alive).slice(0, need);
    for (const p of voters) {
      const chip = await waitFor(() =>
        $$(".modal .chips .chip").find((c) => txt(c).includes(p.seat + "号")));
      await click(chip, 180);
    }
    await sleep(700);
    await clickText(".modal .actions button", "结算投票", 1400);
  }

  async function gmDemo() {
    await titleCard("说书人（GM）助手", "8 人局 · 经典后宫 · 备局 → 首夜 → 处决 → 继任 → 终局", 2600);

    caption("主页可恢复历史对局，也可以<b>开新对局</b>");
    await sleep(1500);
    await clickText("button", "开新对局", 800);

    caption("<b>第一步 · 选择人数</b>：括号内为 村民 / 外来者 / 爪牙 / 恶魔 的基础配置");
    await sleep(1500);
    const b8 = await waitFor(() =>
      $$(".count-btn").find((b) => b.childNodes[0].textContent.trim() === "8"));
    await click(b8, 900);

    caption("<b>第二步 · 圆桌录入姓名</b>：座位顺序会影响邻座类技能（小允子 / 三阿哥 / 浣碧）");
    await sleep(1400);
    for (let i = 0; i < 8; i++) {
      const inp = await waitFor(() => document.querySelector(`input[data-i="${i}"]`));
      await type(inp, NAMES[i]);
    }
    await sleep(500);
    await clickText("button", "确认名单，分配角色", 1100);

    caption("<b>第三步 · 角色分配</b>：选一套预设板子，引擎按人数半随机发牌");
    await sleep(1600);
    const sel = await waitEl(".side-panel select");
    await hover(sel, 460); pulse(); await sleep(300);
    sel.value = "jingdian";
    sel.dispatchEvent(new Event("change", { bubbles: true }));
    await sleep(1100);
    await clickText(".side-panel button", "重新分配", 1200);

    caption("圆桌实时显示配置校验（村民/外来者/爪牙/恶魔）与<b>说书人提示</b>");
    await sleep(2600);

    // 让果郡王进场，顺便演示“更换角色”
    if (!state.game.seats.some((s) => s.role === "guojunwang")) {
      const keep = ["wenshichu", "jinxi", "yelanyi"];
      const idx = state.game.seats.findIndex(
        (s) => state.roleById[s.role].team === "townsfolk" && !keep.includes(s.role));
      caption("发好的牌可以<b>两两交换</b>，也可以<b>更换</b>为未使用的角色 —— 这里换入果郡王");
      await sleep(1800);
      await click(seatEls()[idx], 700);
      await clickText(".detail-box button", "更换角色", 800);
      const opt = await waitFor(() =>
        $$(".modal .role-opt").find((o) => txt(o).includes("果郡王")));
      await click(opt, 1200);
    }

    caption("配置就绪，开始游戏");
    await sleep(1200);
    await clickText(".side-panel button", "开始游戏", 1200);

    // ---- 首夜 ----
    await waitEl(".screen-title", "第 1 个夜晚");
    caption("<b>第 1 夜（首夜）</b>：左侧是夜晚顺序，右侧逐步收集每个角色的行动");
    await sleep(2400);
    await runNight();

    // ---- 第 1 天：处决皇上 ----
    const demonSeat = state.view.seats.find((s) => s.is_demon && s.alive).seat;
    await runDay(demonSeat,
      "<b>第 1 天</b>：好人锁定了皇上 —— 提名自动判定一丈红 / 纯元皇后触发");
    caption("皇上被处决 —— <b>皇后秘密继任为新恶魔</b>（只有说书人知道，见事件记录）");
    await sleep(3000);
    await clickText(".side-panel button", "进入夜晚", 1200);

    // ---- 第 2 夜：守护挡刀 ----
    await waitEl(".screen-title", "第 2 个夜晚");
    caption("<b>第 2 夜</b>：继任的皇后只剩基础夜杀，而果郡王的守护正好挡在刀口上");
    await sleep(2600);
    await runNight();

    // ---- 第 2 天：处决皇后 ----
    const q = state.view.seats.find((s) => s.is_demon && s.alive);
    await runDay(q.seat, "<b>第 2 天</b>：平安夜暴露了继任者，好人提名皇后");

    await waitEl(".end-banner");
    capOff();
    caption("<b>恶魔已死且无人继任 —— 善良阵营获胜</b>，终局公开全部身份与完整事件记录");
    await sleep(4500);
    capOff();
    await sleep(900);
    finish();
  }

  // ---------------- 段二：无说书人模式 ----------------

  async function playDemo() {
    // 玩家端是手机版式（620px 宽），录屏时放大一点填满画面
    document.getElementById("app").style.zoom = "1.35";
    await titleCard("无说书人模式", "机器人担任 GM · 每人一台设备 · 自动发身份与结算", 2600);

    caption("玩家端 <b>/play.html</b>：只选人数即可建房，房间号发给朋友");
    await sleep(1800);
    const b7 = await waitFor(() =>
      $$(".count-btn").find((b) => txt(b) === "7"));
    await click(b7, 1200);

    const room = await waitFor(() => state.room);
    caption("房间号已生成 —— 其余 6 位玩家在各自手机上输入它入座");
    await sleep(1800);

    // 我先入座
    const nameInput = await waitEl(".pcard input.gid-input");
    await type(nameInput, "沐白");
    const mySeat = 1;
    await click(await waitFor(() =>
      $$(".lobby-seat")[mySeat - 1].querySelector("button")), 900);

    // 其余玩家由脚本代打（模拟另外 6 台设备）
    const others = ["青禾", "晚晴", "临舟", "小满", "如意", "砚书"];
    const post = (p, b) =>
      fetch(`/api/rooms/${room}${p}`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(b),
      }).then((r) => r.json());
    const viewOf = (nm) =>
      fetch(`/api/rooms/${room}/view?name=${encodeURIComponent(nm)}`)
        .then((r) => r.json()).catch(() => null);

    caption("朋友们陆续入座（此处由脚本模拟另外 6 台设备）");
    for (let i = 0; i < others.length; i++) {
      await post("/join", { name: others[i], seat: i + 2 });
      await sleep(260);
      if (i % 2) poll(true);
    }
    poll(true);
    await sleep(800);

    caption("全员<b>就绪</b>后机器人自动抽板子、发身份");
    await sleep(1300);
    await clickText(".actions button", "我已就绪", 600);
    for (const nm of others) { await post("/ready", { name: nm, ready: true }); await sleep(200); }
    poll(true);

    await waitFor(() => state.view && state.view.phase !== "lobby", 20000);
    await sleep(800);
    caption("每人一张<b>私人身份卡</b>：技能说明 + 邪恶阵营互认（善良玩家看不到）");
    await sleep(3400);

    // 让其余玩家把当前阶段该做的事做完（模拟他们各自在手机上操作）
    let botRot = 0;
    // 机器人不要每次都挑第一个选项（否则火力全集中在 1 号，也就是“我”）
    function botTargets(step) {
      const opts = step.options || [];
      const pool = opts.filter((o) => o.seat !== 1);
      const use = pool.length >= step.count ? pool : opts;
      const out = [];
      for (let k = 0; k < step.count; k++) out.push(use[(botRot + k) % use.length].seat);
      botRot++;
      return [...new Set(out)].length === step.count ? out : opts.slice(0, step.count).map((o) => o.seat);
    }

    async function botTick(names, opts) {
      const o = opts || {};
      for (const nm of names) {
        const pv = await viewOf(nm);
        if (!pv || pv.phase === "ended") return;
        if (pv.phase === "night") {
          if (pv.night && pv.night.my_decision) {
            await post("/night/decision", { name: nm, value: pv.night.my_decision.options[0].value });
            continue;
          }
          for (const s of (pv.night && pv.night.my_steps) || []) {
            if (s.done) continue;
            await post("/night/action", {
              name: nm, step_id: s.id, no_action: false,
              targets: botTargets(s),
            }).catch(() => {});
          }
        } else if (pv.phase === "day") {
          const d = pv.day || {};
          if (d.open_vote) {
            if (d.open_vote.eligible.includes(pv.my_seat) &&
                d.open_vote.my_vote == null) {
              await post("/day/vote", { name: nm, vote: true }).catch(() => {});
              if (o.slow) { await sleep(280); poll(true); }
            }
          } else if (!d.ended && o.nominate && pv.me && pv.me.can_nominate) {
            const cands = pv.seats.filter(
              (s) => s.alive && s.seat !== pv.my_seat && !d.nominees.includes(s.seat));
            const tgt = cands.find((s) => s.seat === o.prefer) ||
              cands.find((s) => s.seat !== 1) || cands[0];
            if (tgt) await post("/day/nominate", { name: nm, nominee: tgt.seat }).catch(() => {});
          }
        }
      }
    }

    // 邪恶玩家的视角里写着谁是恶魔 —— 快进阶段用它来收束剧情
    async function demonSeat(names) {
      for (const nm of names) {
        const pv = await viewOf(nm);
        const t = pv && pv.me && (pv.me.evil_team || []).find((q) => q.demon);
        if (t) return t.seat;
      }
      return null;
    }

    const everyone = ["沐白", ...others];
    let votes = 0, shownPrivate = false;

    // 把某张卡片滚到画面中间并停留，让观众看清内容
    const pcard = (t) => $$(".pcard").find((c) => txt(c).includes(t));
    async function reveal(t, capHtml, hold) {
      const el = pcard(t);
      if (!el) return;
      caption(capHtml);
      el.scrollIntoView({ block: "center", behavior: "smooth" });
      await sleep(hold || 3000);
    }

    for (let guard = 0; guard < 60 && votes < 2; guard++) {
      const v = state.view;
      if (!v || v.phase === "ended") break;

      if (v.phase === "night") {
        const night = v.night || {};
        if (night.my_decision) {
          caption("轮到<b>玩家自己</b>裁定的分支，机器人把选择直接弹给对应玩家");
          await sleep(1100);
          await click(await waitEl(".chips .chip"), 800);
        } else if ((night.my_steps || []).some((s) => !s.done)) {
          caption("<b>夜晚</b>：只有需要行动的人收到操作面板，其他人显示「安心闭眼」");
          const s = night.my_steps.find((x) => !x.done);
          for (let k = 0; k < s.count; k++) {
            const chip = await waitFor(() => {
              const cs = $$(".step-box .chips .chip").filter((c) => !c.classList.contains("on"));
              return cs[k] || cs[0];
            });
            await click(chip, 260);
          }
          await click(await waitFor(() =>
            $$(".step-box .actions button.primary").find((b) => !b.disabled)), 700);
        } else {
          if (!night.resolving)
            caption("夜晚行动全部提交后，机器人按规则自动结算死亡链、守护、醉毒与情报");
          await botTick(others);
          await sleep(600);
          poll(true);
          await sleep(600);
        }
      } else if (v.phase === "day") {
        const d = v.day || {};
        const me = v.me || {};
        if (!shownPrivate && pcard("私密信息")) {
          shownPrivate = true;
          await reveal("私密信息",
            "夜里收到的情报只出现在<b>本人</b>屏幕上；若被醉/毒，机器人会替说书人<b>伪造假情报</b>", 3800);
          window.scrollTo({ top: 0, behavior: "smooth" });
          await sleep(800);
        }
        if (d.open_vote) {
          caption("<b>举手投票</b>：票型保密，只同步已表态人数；全员投完才一并公开并结算");
          await sleep(1400);
          if (d.open_vote.eligible.includes(v.my_seat) &&
              d.open_vote.my_vote == null) {
            await click(await waitEl(".vote-box .actions button", "赞成处决"), 700);
          } else if (!me.alive) {
            caption("已出局的玩家仍留在牌桌上：还有一张<b>幽灵票</b>可用，用完就只能围观");
            await sleep(1600);
          }
          await botTick(others, { slow: true });
          await sleep(1600);
          poll(true);
          votes++;
          if (votes === 1) {
            await sleep(900);
            await reveal("公告", "处决结果、死亡、投票明细都会<b>广播</b>到每个人的公告栏", 3400);
          }
        } else if (d.ended) {
          await sleep(1000);
          poll(true);
        } else if (me.can_nominate && $$(".step-box .chips .chip").length) {
          caption("<b>白天</b>：每人每天一次提名，被提名过的人不会被重复提名");
          await sleep(1400);
          const chips = $$(".step-box .chips .chip");
          // 候选里也包含自己，别把自己推上断头台
          await click(chips.find((c) => !txt(c).startsWith(v.my_seat + "号")) || chips[0], 420);
          await click(await waitEl(".step-box .actions button", "提名并发起投票"), 1000);
        } else {
          caption("轮到别的玩家发起提名 —— 每台设备上都会同时弹出投票面板");
          await sleep(900);
          await botTick(others, { nominate: true });
          await sleep(600);
          poll(true);
          await sleep(600);
        }
      }
    }

    // 后半程快进：机器人说书人继续自动结算，直到分出胜负
    if (!state.view || state.view.phase !== "ended") {
      caption("此后由脚本快进 —— 机器人说书人全程自动结算，直接看终局");
      const dseat = await demonSeat(everyone);
      for (let guard = 0; guard < 150; guard++) {
        if (state.view && state.view.phase === "ended") break;
        await botTick(everyone, { nominate: true, prefer: dseat });
        poll(true);
        await sleep(130);
      }
    }

    await waitFor(() => state.view && state.view.phase === "ended", 25000);
    await sleep(600);
    window.scrollTo({ top: 0, behavior: "smooth" });
    caption("分出胜负 —— 全程没有真人说书人");
    await sleep(2600);
    const det = document.querySelector(".pcard details");
    if (det) det.setAttribute("open", "");
    await reveal("身份公开", det
      ? "终局<b>公开全部身份</b>，并附上机器人说书人的<b>隐藏记录</b>供复盘"
      : "终局<b>公开全部身份</b>与完整公告记录", 4600);
    capOff();
    await sleep(900);
    finish();
  }

  // ---------------- 启动 ----------------

  const role = (document.currentScript && document.currentScript.dataset.role) ||
    (location.pathname.includes("play") ? "play" : "gm");

  const run = role === "play" ? playDemo : gmDemo;
  setTimeout(() => {
    run().catch((e) => {
      caption("演示脚本出错：" + e.message);
      console.error("[demo]", e);
      window.__demoError = String(e && e.stack || e);
      finish();
    });
  }, ms(900));
})();
