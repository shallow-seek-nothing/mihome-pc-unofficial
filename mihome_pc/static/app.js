/* 米家 PC 前端 —— 原生 JS，无框架、无构建。
   语法刻意保守（不用 ?. / ??），Win7 上的 Chromium 109 及更老的
   发行版浏览器都能直接跑。 */
(function () {
  "use strict";

  var state = {
    devices: [],
    rooms: [],
    home: "",
    filterRoom: "",
    current: null,
    status: null,
    pollTimer: null,
    qrTimer: null,
    hideReadonly: false,
    query: "",
    scenes: null,
    homeStatus: null,
    rowUpdaters: {},
    interacting: false
  };

  // ---------- 小工具 ----------
  function $(id) { return document.getElementById(id); }

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) { node.className = cls; }
    if (text !== undefined && text !== null) { node.textContent = String(text); }
    return node;
  }

  function toast(message) {
    var box = $("toast");
    box.textContent = message;
    box.hidden = false;
    window.clearTimeout(box._timer);
    box._timer = window.setTimeout(function () { box.hidden = true; }, 2200);
  }

  function api(path, options) {
    options = options || {};
    return fetch(path, options).then(function (response) {
      return response.json().then(function (payload) {
        if (!response.ok) {
          throw new Error(payload && payload.error ? payload.error : response.status);
        }
        return payload;
      });
    });
  }

  function postJSON(path, body) {
    return api(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {})
    });
  }

  // ---------- 主题 ----------
  function currentTheme() {
    return document.documentElement.getAttribute("data-theme") || "light";
  }

  function syncToggle() {
    var button = $("theme-btn");
    if (!button) { return; }
    var dark = currentTheme() === "dark";
    button.classList.toggle("is-night", dark);
    button.setAttribute("aria-pressed", dark ? "true" : "false");
    button.title = dark ? "切换到白天" : "切换到夜间";
  }

  function toggleTheme() {
    var next = currentTheme() === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    try { window.localStorage.setItem("mihome-theme", next); } catch (e) { /* 隐私模式 */ }
    syncToggle();
  }

  // ---------- 设备列表 / 首页 ----------
  function loadStatus() {
    return api("/api/status").then(function (payload) {
      state.status = payload;
      var pill = $("status-pill");
      if (payload.demo) {
        pill.textContent = "演示模式";
        pill.className = "pill";
        $("login-btn").hidden = false;
      } else if (payload.logged_in) {
        pill.textContent = "已登录";
        pill.className = "pill ok";
        $("login-btn").hidden = true;
      } else {
        pill.textContent = "未登录";
        pill.className = "pill off";
        $("login-btn").hidden = false;
      }
      return payload;
    });
  }

  function loadDevices() {
    return api("/api/devices").then(function (payload) {
      state.devices = payload.devices || [];
      state.rooms = payload.rooms || [];
      state.home = payload.home || "";
      $("home-name").textContent = state.home || "PC";
      renderHome();
      loadHomeStatus();
      return state.devices;
    });
  }

  function loadHomeStatus() {
    if (!state.status || state.status.demo) { return; }
    api("/api/home-status").then(function (payload) {
      state.homeStatus = payload;
      var button = $("status-btn");
      var total = payload.total || 0;
      $("status-btn-text").textContent = total > 0 ? "运行中 " + total : "";
      button.hidden = total === 0;
      button.classList.toggle("has-fault", (payload.groups || []).some(function (g) {
        return g.name === "fault";
      }));
    }).catch(function () { /* 状态是可选的，失败不打扰用户 */ });
  }

    // 账号与同步：账号/网关/数据目录 + 插件文案覆盖情况
  function openAccount() {
    $("account-modal").hidden = false;
    var body = $("account-body");
    body.innerHTML = "";
    body.appendChild(el("p", "hint", "\u6b63\u5728\u8bfb\u53d6\u2026"));
    // 两个请求各自兜底：/api/meta 要打云端，网络不好或 token 过期时会 401。
    // 这种情况下面板仍要能用 —— 至少「退出登录」要能点到，不能一起失败。
    Promise.all([
      api("/api/status").catch(function () { return null; }),
      api("/api/meta").catch(function () { return null; })
    ]).then(function (res) {
      var status = res[0] || state.status || {};
      var meta = res[1] || {};
      body.innerHTML = "";
      var rows = [
        ["\u767b\u5f55", status.demo ? "\u6f14\u793a\u6a21\u5f0f\uff08\u672a\u767b\u5f55\uff09"
          : "\u5df2\u767b\u5f55" + (meta.user_id ? "\uff08userId " + meta.user_id + "\uff09" : "")],
        ["\u7248\u672c", meta.version || "\u2014"],
        ["\u7f51\u5173", meta.base_url || "\u2014"],
        ["\u6570\u636e\u76ee\u5f55", meta.data_dir || "\u2014"]
      ];
      rows.forEach(function (pair) {
        var row = el("div", "meta-row");
        row.appendChild(el("span", "meta-key", pair[0]));
        row.appendChild(el("span", "meta-value", pair[1]));
        body.appendChild(row);
      });
      if (!res[0] && !res[1]) {
        body.appendChild(el("p", "hint",
          "\u8d26\u53f7\u4fe1\u606f\u8bfb\u53d6\u5931\u8d25\uff1a\u670d\u52a1\u672a\u54cd\u5e94\uff0c\u8bf7\u91cd\u5f00\u754c\u9762"));
        return;
      }
      // 操作按钮紧跟账号行：面板内容会滚，退出登录不能藏在最底下
      if (status.credentials_error) {
        body.appendChild(el("p", "hint", status.credentials_error));
      }
      addAccountActions(body, status);
      if (!res[1]) {
        body.appendChild(el("p", "hint",
          "\u4e91\u7aef\u8d26\u53f7\u4fe1\u606f\u6682\u65f6\u8bfb\u4e0d\u5230\uff08\u7f51\u7edc\u6216 token\uff09\uff0c\u8bbe\u5907\u5217\u8868\u53ef\u80fd\u4e5f\u52a0\u8f7d\u4e0d\u51fa\u6765\u3002"));
      }
      var plugins = meta.plugins || [];
      body.appendChild(el("h4", "meta-head", "\u63d2\u4ef6\u6587\u6848"));
      if (!plugins.length) {
        body.appendChild(el("p", "hint",
          "\u5c1a\u65e0\u63d2\u4ef6\u6587\u6848\uff1a\u767b\u5f55\u540e\u542f\u52a8\u65f6\u4f1a\u5728\u540e\u53f0\u81ea\u52a8\u540c\u6b65\u3002"));
      } else {
        plugins.forEach(function (item) {
          var row = el("div", "meta-row");
          row.appendChild(el("span", "meta-key", item.model));
          row.appendChild(el("span", "meta-value", item.texts + " \u6761\u6587\u6848"));
          body.appendChild(row);
        });
      }
      body.appendChild(el("p", "hint",
        "\u63d2\u4ef6\u6587\u6848\u7528\u4e8e\u679a\u4e3e\u6807\u7b7e\u4e0e\u6545\u969c\u7801\u5bf9\u7167\uff1b\u672a\u540c\u6b65\u7684\u578b\u53f7\u4f7f\u7528\u5185\u7f6e\u8bcd\u5178\u3002"));
      loadMembers(body);
    });
  }

  // 账号与同步里的操作：演示模式给「扫码登录」，已登录给「退出登录」。
  // 退出只删本机的 credentials.json，不动手机米家 App 的登录态。
  function addAccountActions(body, status) {
    if (!status || status.demo) {
      var login = el("button", "account-action",
        "\u626b\u7801\u767b\u5f55\uff08\u5f53\u524d\u4e3a\u6f14\u793a\u6a21\u5f0f\uff09");
      login.type = "button";
      login.addEventListener("click", function () {
        $("account-modal").hidden = true;
        openLogin();
      });
      body.appendChild(login);
      return;
    }
    var button = el("button", "account-action danger", "\u9000\u51fa\u767b\u5f55");
    button.type = "button";
    var armed = false;
    var timer = null;
    button.addEventListener("click", function () {
      if (!armed) {
        // 两下确认，不用 window.confirm（弹窗会挡住键盘/焦点恢复）
        armed = true;
        button.textContent = "\u518d\u70b9\u4e00\u6b21\u786e\u8ba4\u9000\u51fa";
        timer = window.setTimeout(function () {
          armed = false;
          button.textContent = "\u9000\u51fa\u767b\u5f55";
        }, 4000);
        return;
      }
      window.clearTimeout(timer);
      armed = false;
      button.disabled = true;
      postJSON("/api/logout", {}).then(function (result) {
        if (!result.ok) { throw new Error(result.error || "\u9000\u51fa\u5931\u8d25"); }
        $("account-modal").hidden = true;
        toast("\u5df2\u9000\u51fa\u767b\u5f55\uff0c\u5207\u56de\u6f14\u793a\u6a21\u5f0f");
        boot();
      }).catch(function (error) {
        button.disabled = false;
        button.textContent = "\u9000\u51fa\u767b\u5f55";
        toast("\u9000\u51fa\u5931\u8d25\uff1a" + error.message);
      });
    });
    body.appendChild(button);
  }

  function openStatus() {
    var payload = state.homeStatus || { total: 0, groups: [] };
    $("status-modal").hidden = false;
    $("status-sub").textContent = payload.total
      ? "共 " + payload.total + " 台设备需要注意"
      : "一切正常";
    var body = $("status-body");
    body.innerHTML = "";
    (payload.groups || []).forEach(function (group) {
      var section = el("div", "status-group");
      var head = el("div", "status-group-head");
      head.appendChild(el("span", "status-group-name", group.label));
      if (group.type_label) {
        head.appendChild(el("span", "status-group-type", group.type_label));
      }
      section.appendChild(head);
      (group.devices || []).forEach(function (device) {
        var row = el("div", "status-row");
        row.appendChild(el("span", "status-device", device.name));
        if (device.text) {
          row.appendChild(el("span", "status-text", device.text));
        }
        row.addEventListener("click", function () {
          $("status-modal").hidden = true;
          openDevice(device.did);
        });
        section.appendChild(row);
      });
      body.appendChild(section);
    });
    if (!(payload.groups || []).length) {
      body.appendChild(el("p", "hint", "没有正在运行或故障的设备"));
    }
  }

  function renderHome() {
    $("home-view").hidden = false;
    $("detail-view").hidden = true;
    if ($("scenes-view")) { $("scenes-view").hidden = true; }
    if ($("messages-view")) { $("messages-view").hidden = true; }
    if ($("page-head")) { $("page-head").hidden = false; }
    setActiveNav("home");
    setPage(state.filterRoom || "\u5168\u90e8\u8bbe\u5907",
            state.filterRoom ? (shownCount() + " \u53f0\u8bbe\u5907") : homeSubtitle());
    refreshCounters();

    // 房间胶囊 + 选中房间的重命名入口
    var chips = $("room-chips");
    chips.innerHTML = "";
    if (state.rooms.length) {
      chips.appendChild(roomChip("", "\u5168\u90e8", state.devices.length));
      state.rooms.forEach(function (room) {
        chips.appendChild(roomChip(room.name, room.name, room.count));
      });
      var addChip = el("button", "room-chip ghost-chip", "\uff0b \u65b0\u5efa\u623f\u95f4");
      addChip.title = "\u5728\u5f53\u524d\u5bb6\u5ead\u91cc\u65b0\u5efa\u4e00\u4e2a\u623f\u95f4";
      addChip.addEventListener("click", createRoom);
      chips.appendChild(addChip);

      var rename = el("button", "room-chip ghost-chip", "\u91cd\u547d\u540d");
      rename.title = state.filterRoom
        ? "\u91cd\u547d\u540d\u300c" + state.filterRoom + "\u300d"
        : "\u5148\u9009\u4e00\u4e2a\u623f\u95f4";
      if (!state.filterRoom) { rename.disabled = true; }
      rename.addEventListener("click", openRoomRename);
      chips.appendChild(rename);

      if (state.filterRoom) {
        var delChip = el("button", "room-chip ghost-chip danger", "\u5220\u9664\u623f\u95f4");
        delChip.title = "\u5220\u9664\u300c" + state.filterRoom + "\u300d\uff08\u8bbe\u5907\u4e0d\u4f1a\u88ab\u5220\uff09";
        delChip.addEventListener("click", deleteRoom);
        chips.appendChild(delChip);
      }
    }

    // 设备卡片
    var grid = $("device-grid");
    grid.innerHTML = "";
    var shown = visibleDevices();
    $("empty-hint").hidden = shown.length > 0;
    if (!shown.length) {
      $("empty-hint").textContent = emptyDeviceHint();
    }
    shown.forEach(function (device) {
      grid.appendChild(deviceTile(device));
    });
  }

  // 空态要说清楚「为什么空」：搜索无结果 / 这个房间没设备 / 账号里就没有设备
  function emptyDeviceHint() {
    if (state.query) {
      return "\u6ca1\u6709\u5339\u914d\u300c" + state.query + "\u300d\u7684\u8bbe\u5907\uff0c\u6362\u4e2a\u5173\u952e\u8bcd\u8bd5\u8bd5";
    }
    if (state.filterRoom && state.devices.length) {
      return "\u300c" + state.filterRoom + "\u300d\u8fd8\u6ca1\u6709\u8bbe\u5907\uff0c\u53ef\u4ee5\u5728\u8bbe\u5907\u8be6\u60c5\u91cc\u628a\u8bbe\u5907\u642c\u8fc7\u6765";
    }
    if (state.devices.length) {
      return "\u6ca1\u6709\u53ef\u663e\u793a\u7684\u8bbe\u5907";
    }
    return "\u8fd8\u6ca1\u6709\u8bbe\u5907\uff1a\u5728\u624b\u673a App \u91cc\u6dfb\u52a0\u540e\uff0c\u8fd9\u91cc\u4f1a\u81ea\u52a8\u51fa\u73b0";
  }

  function visibleDevices() {
    var query = (state.query || "").trim().toLowerCase();
    return state.devices.filter(function (device) {
      if (state.filterRoom && device.room !== state.filterRoom) { return false; }
      if (!query) { return true; }
      var haystack = [device.name, device.model, device.room, device.category]
        .join(" ").toLowerCase();
      return haystack.indexOf(query) >= 0;
    });
  }

  function shownCount() {
    return visibleDevices().length;
  }

  function homeSubtitle() {
    var online = state.devices.filter(function (d) { return d.online; }).length;
    var parts = [state.devices.length + " \u53f0\u8bbe\u5907"];
    if (state.devices.length) {
      parts.push(online + " \u53f0\u5728\u7ebf");
    }
    if (state.rooms.length) {
      parts.push(state.rooms.length + " \u4e2a\u623f\u95f4");
    }
    return parts.join(" \u00b7 ");
  }

  function setPage(title, subtitle) {
    var heading = $("page-title");
    var sub = $("page-sub");
    if (heading) { heading.textContent = title; }
    if (sub) { sub.textContent = subtitle || ""; }
  }

  function setActiveNav(name) {
    var newTimer = $("new-timer-btn");
    if (newTimer) { newTimer.hidden = name !== "scenes"; }
    var mark = $("message-read-btn");
    if (mark) { mark.hidden = name !== "messages"; }
    var sceneBtn = $("new-scene-btn");
    if (sceneBtn) { sceneBtn.hidden = name !== "scenes"; }
    Array.prototype.forEach.call(document.querySelectorAll("[data-nav]"), function (item) {
      var active = item.getAttribute("data-nav") === name;
      if (active) { item.classList.add("active"); }
      else { item.classList.remove("active"); }
    });
    // 搜索只对设备列表有意义
    var search = $("search-input");
    if (search) { search.hidden = name !== "home"; }
  }

  function refreshCounters() {
    var devices = $("nav-device-count");
    if (devices) { devices.textContent = state.devices.length; }
  }

  // 侧栏的场景数与未读数：不进去也要准，所以启动时各取一次（服务端有缓存）
  // 未读数轮询：没有长连接，就每 45 秒问一次（页面不可见时跳过）
  var unreadTimer = null;

  function startUnreadPolling() {
    if (unreadTimer) { return; }
    unreadTimer = window.setInterval(pollUnread, 45000);
    window.addEventListener("focus", pollUnread);
  }

  function pollUnread() {
    if (document.hidden) { return; }
    api("/api/messages/unread").then(function (payload) {
      var node = $("nav-message-count");
      var unread = (payload && payload.unread) || 0;
      if (node) { node.textContent = unread; }
      var showing = $("messages-view") && !$("messages-view").hidden;
      if (showing) { refreshMessages(); }
    }).catch(function () { /* 轮询失败安静重试 */ });
  }

  function refreshBadges() {
    api("/api/scenes").then(function (payload) {
      state.scenes = payload;
      var node = $("nav-scene-count");
      if (node) { node.textContent = (payload.scenes || []).length; }
    }).catch(function () { /* 侧栏数字不是关键路径 */ });
    api("/api/messages").then(function (payload) {
      var node = $("nav-message-count");
      if (node) { node.textContent = payload.unread || 0; }
    }).catch(function () { /* 同上 */ });
  }

  // ---------- 房间：新建 / 删除 / 重命名 / 搬设备 ----------
  function createRoom() {
    var name = window.prompt("\u65b0\u623f\u95f4\u540d\u79f0", "");
    if (name === null) { return; }
    name = name.trim();
    if (!name) { return; }
    postJSON("/api/room/add", { name: name }).then(function (result) {
      if (!result.ok) {
        toast("\u65b0\u5efa\u5931\u8d25\uff1a" + (result.message || ("code " + result.code)));
        return;
      }
      toast("\u5df2\u65b0\u5efa\u300c" + name + "\u300d");
      state.filterRoom = name;
      loadDevices().then(function () { renderHome(); });
    }).catch(function (error) { toast("\u65b0\u5efa\u5931\u8d25\uff1a" + error.message); });
  }

  function deleteRoom() {
    var room = null;
    state.rooms.forEach(function (item) {
      if (item.name === state.filterRoom) { room = item; }
    });
    if (!room) { return; }
    var hint = room.count
      ? "\u300c" + room.name + "\u300d\u91cc\u8fd8\u6709 " + room.count + " \u53f0\u8bbe\u5907\uff0c\u5220\u9664\u540e\u5b83\u4eec\u4f1a\u53d8\u6210\u300c\u672a\u5206\u914d\u300d\u3002\u786e\u5b9a\uff1f"
      : "\u5220\u9664\u300c" + room.name + "\u300d\uff1f";
    if (!window.confirm(hint)) { return; }
    postJSON("/api/room/delete", { room_id: room.id }).then(function (result) {
      if (!result.ok) {
        toast("\u5220\u9664\u5931\u8d25\uff1a" + (result.message || ("code " + result.code)));
        return;
      }
      toast("\u5df2\u5220\u9664\u300c" + room.name + "\u300d");
      state.filterRoom = "";
      loadDevices().then(function () { renderHome(); });
    }).catch(function (error) { toast("\u5220\u9664\u5931\u8d25\uff1a" + error.message); });
  }

  function moveDeviceToRoom(did, roomId, select) {
    if (!roomId) { return; }
    select.disabled = true;
    postJSON("/api/room/move", { did: did, room_id: roomId }).then(function (result) {
      if (!result.ok) {
        toast("\u79fb\u52a8\u5931\u8d25\uff1a" + (result.message || ("code " + result.code)));
        select.disabled = false;
        return;
      }
      toast("\u5df2\u79fb\u5230\u65b0\u623f\u95f4");
      loadDevices().then(function () { openDevice(did); });
    }).catch(function (error) {
      toast("\u79fb\u52a8\u5931\u8d25\uff1a" + error.message);
      select.disabled = false;
    });
  }
  function openRoomRename() {
    if (!state.filterRoom) {
      toast("\u5148\u9009\u4e00\u4e2a\u623f\u95f4\uff08\u4e0d\u80fd\u662f\u300c\u5168\u90e8\u300d\uff09");
      return;
    }
    var room = null;
    state.rooms.forEach(function (item) {
      if (item.name === state.filterRoom) { room = item; }
    });
    if (!room) { toast("\u627e\u4e0d\u5230\u8fd9\u4e2a\u623f\u95f4"); return; }
    var name = window.prompt("\u623f\u95f4\u540d\u79f0", room.name);
    if (name === null) { return; }
    name = name.trim();
    if (!name || name === room.name) { return; }
    postJSON("/api/room/rename", { room_id: room.id, name: name }).then(function (result) {
      if (result.ok) {
        toast("\u5df2\u6539\u540d\u4e3a\u300c" + name + "\u300d");
        state.filterRoom = name;
        loadDevices().then(function () { renderHome(); });
      } else {
        toast("\u91cd\u547d\u540d\u5931\u8d25\uff1a" + (result.message || ("code " + result.code)));
      }
    }).catch(function (error) { toast("\u91cd\u547d\u540d\u5931\u8d25\uff1a" + error.message); });
  }

  // 家庭共享（只读）：谁有这台家里的权限
  function loadMembers(host) {
    api("/api/members").then(function (payload) {
      var members = (payload && payload.members) || [];
      if (!members.length) { return; }
      host.appendChild(el("h4", "meta-head", "\u5bb6\u5ead\u5171\u4eab"));
      members.forEach(function (member) {
        var row = el("div", "meta-row member-row");
        var nameCell = el("span", "meta-key", member.name);
        nameCell.title = member.name;
        row.appendChild(nameCell);
        var bits = [member.role_label];
        if (member.joined) { bits.push(formatDate(member.joined) + " \u52a0\u5165"); }
        row.appendChild(el("span", "meta-value", bits.join(" \u00b7 ")));
        host.appendChild(row);
      });
      host.appendChild(el("p", "hint",
        "\u5171\u4eab\u6210\u5458\u53ea\u8bfb\uff1b\u9080\u8bf7\u4e0e\u79fb\u9664\u8bf7\u7528\u624b\u673a App\u3002"));
    }).catch(function () { /* 共享信息拿不到不影响其它内容 */ });
  }

  // ---------- 消息中心 ----------
  function openMessages() {
    $("home-view").hidden = true;
    $("detail-view").hidden = true;
    if ($("scenes-view")) { $("scenes-view").hidden = true; }
    $("messages-view").hidden = false;
    if ($("page-head")) { $("page-head").hidden = false; }
    stopPolling();
    setActiveNav("messages");
    setPage("\u6d88\u606f", "\u6b63\u5728\u8bfb\u53d6\u2026");
    api("/api/messages?refresh=1").then(renderMessages).catch(function (error) {
      toast("\u6d88\u606f\u52a0\u8f7d\u5931\u8d25\uff1a" + error.message);
    });
  }

  function renderMessages(payload) {
    state.messages = (payload && payload.messages) || [];
    state.messageTypes = (payload && payload.types) || [];
    state.messageUnread = (payload && payload.unread) || 0;
    var count = $("nav-message-count");
    if (count) { count.textContent = state.messageUnread; }
    renderMessageTypes();
    renderMessageList();
  }

  function renderMessageList() {
    var messages = filteredMessages();
    var host = $("message-list");
    host.innerHTML = "";
    host.hidden = !messages.length;
    var empty = $("message-empty");
    empty.hidden = messages.length > 0;
    empty.textContent = state.messageType
      ? "\u8fd9\u4e2a\u7c7b\u578b\u4e0b\u6ca1\u6709\u6d88\u606f"
      : "\u8fd8\u6ca1\u6709\u6d88\u606f\uff1b\u8bbe\u5907\u5b8c\u6210\u4efb\u52a1\u3001"
        + "\u573a\u666f\u6267\u884c\u5931\u8d25\u65f6\u4f1a\u51fa\u73b0\u5728\u8fd9\u91cc";
    setPage("\u6d88\u606f", messages.length + " \u6761\u6700\u8fd1\u6d88\u606f"
            + (state.messageUnread ? " \u00b7 " + state.messageUnread
                                     + " \u6761\u672a\u8bfb" : ""));
    messages.forEach(function (item) { host.appendChild(messageRow(item)); });
  }

  // 类型筛选：全部 / 设备消息 / 场景通知……
  function filteredMessages() {
    var all = state.messages || [];
    if (state.messageType === undefined || state.messageType === null
        || state.messageType === "") {
      return all;
    }
    return all.filter(function (item) {
      return String(item.type) === String(state.messageType);
    });
  }

  function renderMessageTypes() {
    var host = $("message-types");
    if (!host) { return; }
    host.innerHTML = "";
    var types = state.messageTypes || [];
    if (types.length < 2) { host.hidden = true; return; }
    host.hidden = false;
    var chip = el("button", "room-chip" + (state.messageType ? "" : " active"),
                 "\u5168\u90e8 " + (state.messages || []).length);
    chip.type = "button";
    chip.addEventListener("click", function () {
      state.messageType = "";
      renderMessageTypes();
      renderMessageList();
    });
    host.appendChild(chip);
    types.forEach(function (item) {
      var button = el("button", "room-chip"
        + (String(state.messageType) === String(item.type) ? " active" : ""),
        item.label + " " + item.count);
      button.type = "button";
      button.addEventListener("click", function () {
        state.messageType = item.type;
        renderMessageTypes();
        renderMessageList();
      });
      host.appendChild(button);
    });
  }

  function messageRow(item) {
    var row = el("div", "message-row" + (item.is_new ? " unread" : ""));
    var head = el("div", "message-head");
    head.appendChild(el("span", "message-type", item.type_label));
    head.appendChild(el("span", "message-title", item.title));
    if (item.is_new) { head.appendChild(el("span", "message-dot")); }
    row.appendChild(head);

    var bits = [];
    if (item.device) { bits.push(item.device); }
    if (item.time) { bits.push(relativeTime(item.time)); }
    if (bits.length) { row.appendChild(el("div", "message-sub", bits.join(" \u00b7 "))); }
    var body = (item.content || "").trim();
    if (body && body !== (item.device || "").trim() && body !== (item.title || "").trim()) {
      row.appendChild(el("p", "message-body", body));
    }

    var remove = el("button", "ghost small message-delete", "\u5220\u9664");
    remove.addEventListener("click", function () { deleteMessage(item, remove); });
    row.appendChild(remove);
    return row;
  }

  function deleteMessage(item, button) {
    if (!window.confirm("\u5220\u9664\u300c" + item.title + "\u300d\uff1f")) { return; }
    button.disabled = true;
    postJSON("/api/messages/delete", { msg_id: item.id }).then(function (result) {
      if (result.ok) {
        toast("\u5df2\u5220\u9664");
        refreshMessages();
      } else {
        toast("\u5220\u9664\u5931\u8d25\uff1a" + (result.message || ("code " + result.code)));
        button.disabled = false;
      }
    }).catch(function (error) {
      toast("\u5220\u9664\u5931\u8d25\uff1a" + error.message);
      button.disabled = false;
    });
  }

  function markRead() {
    var button = $("message-read-btn");
    if (button) { button.disabled = true; }
    postJSON("/api/messages/read", {}).then(function (result) {
      if (result.ok) {
        toast("\u5df2\u6807\u4e3a\u5df2\u8bfb");
        refreshMessages();
      } else {
        toast("\u64cd\u4f5c\u5931\u8d25\uff1a" + (result.message || ("code " + result.code)));
      }
    }).catch(function (error) { toast("\u64cd\u4f5c\u5931\u8d25\uff1a" + error.message); })
      .then(function () { if (button) { button.disabled = false; } });
  }

  function refreshMessages() {
    api("/api/messages?refresh=1").then(renderMessages).catch(function (error) {
      toast("\u5237\u65b0\u5931\u8d25\uff1a" + error.message);
    });
  }

  function renameDevice(info) {
    var name = window.prompt("\u8bbe\u5907\u540d\u79f0", info.name || "");
    if (name === null) { return; }
    name = name.trim();
    if (!name || name === info.name) { return; }
    postJSON("/api/device/rename", { did: info.did, name: name }).then(function (result) {
      if (!result.ok) {
        toast("\u6539\u540d\u5931\u8d25\uff1a" + (result.message || ("code " + result.code)));
        return;
      }
      toast("\u5df2\u6539\u540d\u4e3a\u300c" + name + "\u300d");
      loadDevices().then(function () { openDevice(info.did); });
    }).catch(function (error) { toast("\u6539\u540d\u5931\u8d25\uff1a" + error.message); });
  }

  function renameHome() {
    var current = $("home-name").textContent;
    var name = window.prompt("\u5bb6\u5ead\u540d\u79f0", current);
    if (name === null) { return; }
    name = name.trim();
    if (!name || name === current) { return; }
    postJSON("/api/home/rename", { name: name }).then(function (result) {
      if (!result.ok) {
        toast("\u6539\u540d\u5931\u8d25\uff1a" + (result.message || ("code " + result.code)));
        return;
      }
      $("home-name").textContent = name;
      toast("\u5bb6\u5ead\u5df2\u6539\u540d");
    }).catch(function (error) { toast("\u6539\u540d\u5931\u8d25\uff1a" + error.message); });
  }

  // 下次执行：精确、工作日估算、节假日不可算 三种情况要分开说
  function nextRunText(trigger) {
    var status = trigger.next_run_status || (trigger.next_run_approx
      ? "approximate_weekday" : "exact");
    if (status === "unavailable_holiday") {
      return "\u6cd5\u5b9a\u8282\u5047\u65e5\u7531\u4e91\u7aef\u5224\u5b9a\uff0c\u672c\u5730\u65e0\u6cd5\u8ba1\u7b97\u4e0b\u6b21\u6267\u884c\u65f6\u95f4";
    }
    if (!trigger.next_run) { return ""; }
    if (status === "approximate_weekday") {
      return "\u9884\u8ba1\u4e0b\u6b21 " + formatDateTime(trigger.next_run)
        + "\uff08\u6309\u5468\u4e00\u81f3\u5468\u4e94\u4f30\u7b97\uff0c\u672a\u8ba1\u8282\u5047\u65e5\uff0f\u8c03\u4f11\uff09";
    }
    return "\u4e0b\u6b21 " + formatDateTime(trigger.next_run);
  }

  // "2026-11-23T08:45:00" -> "11-23 08:45"（今年内不带年\u4efd，跨\u5e74\u8865\u4e0a）
  function formatDateTime(iso) {
    var parts = String(iso || "").split("T");
    if (parts.length !== 2) { return iso; }
    var year = Number(parts[0].slice(0, 4));
    var month = Number(parts[0].slice(5, 7));
    var day = Number(parts[0].slice(8, 10));
    var clock = parts[1].slice(0, 5);
    var prefix = (year === new Date().getFullYear()) ? "" : year + "-";
    return prefix + pad2(month) + "-" + pad2(day) + " " + clock;
  }

  // 加入时间这类「久远的日期」用绝对日期更清楚，相对时间会变成「2733 天前」
  function formatDate(seconds) {
    var date = new Date(seconds * 1000);
    return date.getFullYear() + "-" + pad2(date.getMonth() + 1) + "-" + pad2(date.getDate());
  }

  function relativeTime(seconds) {
    var delta = Math.max(0, Math.round(Date.now() / 1000 - seconds));
    if (delta < 90) { return "\u521a\u521a"; }
    if (delta < 3600) { return Math.round(delta / 60) + " \u5206\u949f\u524d"; }
    if (delta < 86400) { return Math.round(delta / 3600) + " \u5c0f\u65f6\u524d"; }
    return Math.round(delta / 86400) + " \u5929\u524d";
  }

  // ---------- 场景 ----------
  function openScenes() {
    $("home-view").hidden = true;
    $("detail-view").hidden = true;
    if ($("messages-view")) { $("messages-view").hidden = true; }
    $("scenes-view").hidden = false;
    if ($("page-head")) { $("page-head").hidden = false; }
    stopPolling();
    setActiveNav("scenes");
    setPage("\u573a\u666f", "\u4e00\u952e\u6267\u884c\u5bb6\u91cc\u7684\u624b\u52a8\u573a\u666f");
    if (state.scenes) { renderScenes(state.scenes); }
    api("/api/scenes").then(function (payload) {
      state.scenes = payload;
      renderScenes(payload);
    }).catch(function (error) {
      toast("\u573a\u666f\u52a0\u8f7d\u5931\u8d25\uff1a" + error.message);
    });
  }

  function renderScenes(payload) {
    var allScenes = (payload && payload.scenes) || [];
    if ($("scene-empty")) {
      $("scene-empty").hidden = allScenes.length > 0;
      $("scene-empty").textContent =
        "\u8fd8\u6ca1\u6709\u53ef\u6267\u884c\u573a\u666f\uff1b\u65b0\u5efa\u9700\u8981\u8d26\u53f7\u91cc\u5df2\u6709\u4e00\u6761\u540c\u7c7b\u573a\u666f\uff08\u65b0\u5efa\u65f6\u4f1a\u590d\u7528\u5b83\u7684\u52a8\u4f5c\u6a21\u677f\uff09\uff0c\u53ef\u5148\u5728\u624b\u673a App \u91cc\u5efa\u4e00\u6761";
    }
    var scenes = (payload && payload.scenes) || [];
    var grid = $("scene-grid");
    grid.innerHTML = "";
    var count = $("nav-scene-count");
    if (count) { count.textContent = scenes.length; }
    $("scene-empty").hidden = scenes.length > 0;
    scenes.forEach(function (scene) { grid.appendChild(sceneCard(scene)); });

    var note = $("scene-note");
    var automations = (payload && payload.automations) || [];
    if (automations.length) {
      note.textContent = "\u4e0b\u9762\u662f\u81ea\u52a8\u5316\uff1a\u5b9a\u65f6\u4f1a\u81ea\u5df1\u8dd1\uff0c\u4e0d\u9700\u8981\u624b\u52a8\u6267\u884c";
      note.hidden = false;
    } else {
      note.hidden = true;
    }
    var host = $("automation-list");
    if (host) {
      host.innerHTML = "";
      host.hidden = !automations.length;
      automations.forEach(function (item) { host.appendChild(automationRow(item)); });
    }
    setPage("\u573a\u666f", scenes.length + " \u4e2a\u53ef\u6267\u884c\u573a\u666f"
            + (automations.length ? " \u00b7 " + automations.length + " \u4e2a\u81ea\u52a8\u5316" : "")
            + (scenes.length > 1 ? " \u00b7 \u62d6\u52a8\u5361\u7247\u53ef\u8c03\u6574\u987a\u5e8f" : ""));
  }

  var draggedSceneId = null;

  function sceneCard(scene) {
    var card = el("div", "scene-card" + (scene.enabled ? "" : " disabled"));
    card.draggable = true;
    card.addEventListener("dragstart", function (event) {
      draggedSceneId = scene.id;
      card.classList.add("dragging");
      if (event.dataTransfer) {
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData("text/plain", scene.id);
      }
    });
    card.addEventListener("dragend", function () {
      card.classList.remove("dragging");
      draggedSceneId = null;
      Array.prototype.forEach.call(
        document.querySelectorAll(".scene-card.drop-target"),
        function (node) { node.classList.remove("drop-target"); });
    });
    card.addEventListener("dragover", function (event) {
      if (!draggedSceneId || draggedSceneId === scene.id) { return; }
      event.preventDefault();
      card.classList.add("drop-target");
    });
    card.addEventListener("dragleave", function () {
      card.classList.remove("drop-target");
    });
    card.addEventListener("drop", function (event) {
      event.preventDefault();
      card.classList.remove("drop-target");
      if (!draggedSceneId || draggedSceneId === scene.id) { return; }
      dropScene(draggedSceneId, scene.id);
    });
    var head = el("div", "scene-head");
    var icon = el("img", "scene-icon");
    icon.alt = "";
    if (scene.icon) {
      icon.src = scene.icon;
      icon.onerror = function () { icon.style.visibility = "hidden"; };
    } else {
      icon.style.visibility = "hidden";
    }
    head.appendChild(icon);

    var titles = el("div", "scene-title");
    var sceneName = el("div", "scene-name", scene.name);
    sceneName.title = scene.name;
    titles.appendChild(sceneName);
    var bits = [];
    if (scene.category_label) { bits.push(scene.category_label); }
    var devices = scene.devices || [];
    if (devices.length) { bits.push(devices.length + " \u4e2a\u8bbe\u5907"); }
    titles.appendChild(el("div", "scene-sub", bits.join(" \u00b7 ")));
    head.appendChild(titles);

    var run = el("button", "scene-run", "\u6267\u884c");
    if (!scene.enabled) {
      run.disabled = true;
      run.textContent = "\u5df2\u7981\u7528";
    }
    run.addEventListener("click", function () { runScene(scene, run); });
    var tools = el("div", "scene-tools");
    tools.appendChild(run);
    head.appendChild(tools);
    card.appendChild(head);

    var foot = el("div", "scene-foot");
    tools = foot;
    var up = el("button", "scene-edit scene-order", "\u2191");
    up.title = "\u5f80\u524d\u79fb\u4e00\u4f4d";
    up.addEventListener("click", function () { moveScene(scene, "up", up); });
    tools.appendChild(up);
    var down = el("button", "scene-edit scene-order", "\u2193");
    down.title = "\u5f80\u540e\u79fb\u4e00\u4f4d";
    down.addEventListener("click", function () { moveScene(scene, "down", down); });
    tools.appendChild(down);
    var edit = el("button", "scene-edit", "\u7f16\u8f91");
    edit.title = "\u6539\u540d\u5b57\u6216\u6539\u52a8\u4f5c";
    edit.addEventListener("click", function () { openSceneEditor(scene); });
    tools.appendChild(edit);
    card.appendChild(foot);

    if (devices.length) {
      card.appendChild(el("div", "scene-devices", devices.join("\u3001")));
    }
    return card;
  }

  function automationRow(item) {
    var row = el("div", "automation" + (item.enabled ? "" : " off"));
    var icon = el("span", "automation-dot");
    row.appendChild(icon);

    var body = el("div", "automation-body");
    var autoName = el("div", "automation-name", item.name);
    autoName.title = item.name;
  body.appendChild(autoName);
    var trigger = item.trigger || {};
    var bits = [];
    if (trigger.label) { bits.push(trigger.label); }
    if (trigger.detail) { bits.push(trigger.detail); }
    if (item.devices && item.devices.length) {
      bits.push(item.devices.slice(0, 2).join("\u3001"));
    }
    body.appendChild(el("div", "automation-sub", bits.join(" \u00b7 ")));
    row.appendChild(body);

    var nextText = nextRunText(trigger);
    if (nextText) {
      row.appendChild(el("div", "automation-next", nextText));
    }

    if (trigger.src === "timer") {
      row.appendChild(timerControls(item));
    }

    var toggle = el("button", "switch" + (item.enabled ? " on" : ""));
    toggle.title = item.enabled ? "\u5df2\u5f00\u542f\uff0c\u70b9\u51fb\u505c\u7528" : "\u5df2\u505c\u7528\uff0c\u70b9\u51fb\u5f00\u542f";
    toggle.addEventListener("click", function () {
      toggleScene(item, !item.enabled, toggle);
    });
    row.appendChild(toggle);
    return row;
  }

  // ---------- 新建定时 ----------
  function openActionDialog(mode, scene) {
    var modal = $("timer-modal");
    if (!modal) { return; }
    state.actionMode = mode === "scene" ? "scene"
      : (mode === "edit" ? "edit" : "timer");
    state.editSceneId = state.actionMode === "edit" && scene ? scene.id : "";
    state.actionDraft = [];
    if (state.actionMode === "edit" && scene) {
      (scene.actions || []).forEach(function (action) {
        state.actionDraft.push({
          label: (action.device || action.did) + " \u00b7 "
            + (action.kind === "action" ? "\u52a8\u4f5c" : "\u8bbe\u7f6e\u5c5e\u6027"),
          spec: {
            did: action.did, kind: action.kind || "prop", siid: action.siid,
            piid: action.piid, aiid: action.aiid,
            value: action.value, inputs: action.inputs
          }
        });
      });
    }
    renderDraft();
    var isScene = state.actionMode === "scene";
    var isEdit = state.actionMode === "edit";
    var hideTime = isScene || isEdit;
    $("timer-title").textContent = isEdit ? "\u7f16\u8f91\u573a\u666f"
      : (isScene ? "\u65b0\u5efa\u573a\u666f" : "\u65b0\u5efa\u5b9a\u65f6");
    $("timer-time-row").hidden = hideTime;
    $("timer-repeat-row").hidden = hideTime;
    if ($("timer-days-row")) {
      $("timer-days-row").hidden = hideTime || $("timer-repeat").value !== "weekly";
    }
    $("timer-msg").hidden = hideTime;
    $("scene-msg").hidden = !hideTime;
    $("timer-name").value = isEdit && scene ? scene.name : "";
    $("timer-create").textContent = isEdit ? "\u4fdd\u5b58" : "\u521b\u5efa";
    state.sceneIcon = isEdit && scene ? (scene.icon || "") : "";
    $("timer-icon-row").hidden = !isScene && !isEdit;
    if (!hideTime) { $("timer-icon-row").hidden = true; }
    else { loadIconPicker(); }
    var deviceSelect = $("timer-device");
    deviceSelect.innerHTML = "";
    // 只列「真有可选动作」的设备，免得默认选中一个手环然后什么都选不了
    state.timerDevices = state.devices.filter(function (device) {
      return timerTargets(device).length > 0;
    });
    state.timerDevices.forEach(function (device) {
      var option = el("option", null, (device.room ? device.room + " \u00b7 " : "")
        + (device.name || device.model || device.did));
      option.value = device.did;
      deviceSelect.appendChild(option);
    });
    if (!state.timerDevices.length) {
      var empty = el("option", null, "\u6ca1\u6709\u53ef\u7528\u7684\u8bbe\u5907");
      empty.value = "";
      deviceSelect.appendChild(empty);
    }
    $("timer-msg").textContent = "\u5b9a\u65f6\u4f1a\u6309\u4e0a\u9762\u7684\u65f6\u95f4\u6bcf\u5929\u6267\u884c\u4e00\u6b21";
    buildWeekdayPicker();
    fillTimerTargets();
    modal.hidden = false;
  }

  // 周几选择：cron 里 0=周日，和 App 的位掩码一致（62=周一..周五，65=周日+周六）
  var WEEKDAY_LABELS = ["\u5468\u65e5", "\u5468\u4e00", "\u5468\u4e8c",
                        "\u5468\u4e09", "\u5468\u56db", "\u5468\u4e94", "\u5468\u516d"];

  function buildWeekdayPicker() {
    var host = $("timer-days");
    if (!host) { return; }
    host.innerHTML = "";
    state.timerDays = state.timerDays || [];
    WEEKDAY_LABELS.forEach(function (label, index) {
      var chip = el("button", "weekday-chip", label);
      chip.type = "button";
      if (state.timerDays.indexOf(index) >= 0) { chip.classList.add("active"); }
      chip.addEventListener("click", function () {
        var at = state.timerDays.indexOf(index);
        if (at >= 0) { state.timerDays.splice(at, 1); chip.classList.remove("active"); }
        else { state.timerDays.push(index); chip.classList.add("active"); }
      });
      host.appendChild(chip);
    });
    var row = $("timer-days-row");
    if (row) { row.hidden = $("timer-repeat").value !== "weekly"; }
  }

  function closeTimerDialog() {
    var modal = $("timer-modal");
    if (modal) { modal.hidden = true; }
  }

  // 可写开关 / 枚举 / 无参动作 —— 只列用户真能选的东西
  function timerTargets(device) {
    var targets = [];
    (device.controls || []).forEach(function (control) {
      if (!control.writable) { return; }
      if (control.widget === "switch") {
        targets.push({ label: control.label + "\uff1a\u5f00", kind: "prop",
                       siid: control.siid, piid: control.piid, value: true });
        targets.push({ label: control.label + "\uff1a\u5173", kind: "prop",
                       siid: control.siid, piid: control.piid, value: false });
      } else if (control.options && control.options.length) {
        targets.push({ label: control.label, kind: "prop-enum", control: control,
                       siid: control.siid, piid: control.piid });
      } else if (control.widget === "command") {
        targets.push({ label: control.label + "\uff08\u547d\u4ee4\uff09", kind: "prop",
                       siid: control.siid, piid: control.piid,
                       value: control.command_value });
      }
    });
    (device.actions || []).forEach(function (action) {
      // 需要参数的动作先不列：以前只看 in_refs，纯 inline 动作 in_refs 为空会漏过去
      if ((action.params || []).length || (action.in_refs || []).length) { return; }
      targets.push({ label: action.label + "\uff08\u52a8\u4f5c\uff09", kind: "action",
                     siid: action.siid, aiid: action.aiid, inputs: [] });
    });
    return targets;
  }

  function currentTimerDevice() {
    var did = $("timer-device").value;
    var list = state.timerDevices || state.devices;
    for (var i = 0; i < list.length; i += 1) {
      if (list[i].did === did) { return list[i]; }
    }
    return null;
  }

  function fillTimerTargets() {
    var device = currentTimerDevice();
    var select = $("timer-target");
    select.innerHTML = "";
    if (!device) { return; }
    var targets = timerTargets(device);
    state.timerTargets = targets;
    if (!targets.length) {
      var empty = el("option", null, "\u8be5\u8bbe\u5907\u6ca1\u6709\u53ef\u9009\u7684\u52a8\u4f5c");
      empty.value = "";
      select.appendChild(empty);
      return;
    }
    targets.forEach(function (target, index) {
      var option = el("option", null, target.label);
      option.value = String(index);
      select.appendChild(option);
    });
    fillTimerValue();
  }

  function fillTimerValue() {
    var row = $("timer-value-row");
    var select = $("timer-value");
    select.innerHTML = "";
    var target = (state.timerTargets || [])[Number($("timer-target").value || 0)];
    if (!target || target.kind !== "prop-enum") {
      row.hidden = true;
      return;
    }
    row.hidden = false;
    (target.control.options || []).forEach(function (pair) {
      var option = el("option", null, pair[1]);
      option.value = String(pair[0]);
      select.appendChild(option);
    });
  }

  function createTimer() {
    if (state.actionMode === "edit") { return saveSceneEdit(); }
    if (state.actionMode === "scene") { return createScene(); }
    var device = currentTimerDevice();
    var target = (state.timerTargets || [])[Number($("timer-target").value || 0)];
    var time = $("timer-time").value;
    if (!device || !target) { toast("\u5148\u9009\u8bbe\u5907\u548c\u52a8\u4f5c"); return; }
    if (!time) { toast("\u5148\u9009\u65f6\u95f4"); return; }
    var parts = time.split(":");
    var repeat = $("timer-repeat") ? $("timer-repeat").value : "daily";
    var days = state.timerDays || [];
    if (repeat === "weekly" && !days.length) {
      toast("\u5148\u9009\u81f3\u5c11\u4e00\u4e2a\u661f\u671f");
      return;
    }
    var payload = {
      repeat: repeat,
      days: repeat === "weekly" ? days : [],
      name: $("timer-name").value || (time + "\u5b9a\u65f6")
    };
    var draft = draftSpecs();
    if (draft.length) {
      payload.actions = draft;
    } else {
      var single = actionPayload(device, target);
      payload.did = single.did;
      payload.kind = single.kind;
      payload.siid = single.siid;
      if (single.piid !== undefined) { payload.piid = single.piid; }
      if (single.aiid !== undefined) { payload.aiid = single.aiid; }
      if (single.value !== undefined) { payload.value = single.value; }
      if (single.inputs !== undefined) { payload.inputs = single.inputs; }
    }
    var button = $("timer-create");
    button.disabled = true;
    postJSON("/api/timer/create", payload).then(function (result) {
      if (result.ok) {
        toast("\u5df2\u521b\u5efa\u5b9a\u65f6");
        closeTimerDialog();
        refreshScenes();
      } else {
        toast("\u521b\u5efa\u5931\u8d25\uff1a" + (result.message || ("code " + result.code)));
      }
    }).catch(function (error) { toast("\u521b\u5efa\u5931\u8d25\uff1a" + error.message); })
      .then(function () { button.disabled = false; });
  }

  // 场景图标：从服务端目录里挑一个
  function loadIconPicker() {
    var host = $("timer-icons");
    if (!host) { return; }
    if (state.iconCatalogue) { renderIcons(); return; }
    host.innerHTML = "";
    api("/api/scene/icons").then(function (payload) {
      state.iconCatalogue = (payload && payload.icons) || [];
      renderIcons();
    }).catch(function () { /* 没有图标目录也不影响保存 */ });
  }

  function renderIcons() {
    var host = $("timer-icons");
    if (!host) { return; }
    host.innerHTML = "";
    (state.iconCatalogue || []).forEach(function (icon) {
      var button = el("button", "icon-choice"
        + (state.sceneIcon === icon.url ? " active" : ""));
      button.type = "button";
      button.title = "\u9009\u8fd9\u4e2a\u56fe\u6807";
      var img = el("img");
      img.alt = "";
      img.src = icon.url;
      img.onerror = function () { button.hidden = true; };
      button.appendChild(img);
      button.addEventListener("click", function () {
        state.sceneIcon = icon.url;
        renderIcons();
      });
      host.appendChild(button);
    });
  }

  // 「再加一个动作」：把当前选择存进草稿，最后一起提交（多动作场景/定时）
  function addDraftAction() {
    var device = currentTimerDevice();
    var target = (state.timerTargets || [])[Number($("timer-target").value || 0)];
    if (!device || !target) { toast("\u5148\u9009\u8bbe\u5907\u548c\u52a8\u4f5c"); return; }
    var spec = actionPayload(device, target);
    var label = (device.name || device.model) + " \u00b7 " + target.label;
    state.actionDraft.push({ label: label, spec: spec });
    renderDraft();
  }

  function renderDraft() {
    var host = $("timer-draft");
    if (!host) { return; }
    host.innerHTML = "";
    var draft = state.actionDraft || [];
    draft.forEach(function (item, index) {
      var row = el("div", "draft-row");
      row.appendChild(el("span", "draft-index", String(index + 1)));
      row.appendChild(el("span", "draft-label", item.label));
      var remove = el("button", "draft-remove", "\u00d7");
      remove.type = "button";
      remove.title = "\u79fb\u9664\u8fd9\u4e2a\u52a8\u4f5c";
      remove.addEventListener("click", function () {
        state.actionDraft.splice(index, 1);
        renderDraft();
      });
      row.appendChild(remove);
      host.appendChild(row);
    });
  }

  function draftSpecs() {
    return (state.actionDraft || []).map(function (item) { return item.spec; });
  }

  function actionPayload(device, target) {
    var payload = {
      did: device.did,
      kind: target.kind === "action" ? "action" : "prop",
      siid: target.siid
    };
    if (payload.kind === "action") {
      payload.aiid = target.aiid;
      payload.inputs = target.inputs || [];
    } else {
      payload.piid = target.piid;
      payload.value = target.kind === "prop-enum"
        ? typedOptionValue(target.control, $("timer-value").value)
        : target.value;
    }
    return payload;
  }

  // 拖拽落点：把 fromId 插到 toId 的位置，然后整份顺序提交
  function refreshAfterScene() {
    window.setTimeout(function () {
      loadDevices().then(function () {
        if ($("home-view") && !$("home-view").hidden) { renderHome(); }
      }).catch(function () { /* 下次刷新再说 */ });
    }, 2500);
  }

  function dropScene(fromId, toId) {
    var scenes = (state.scenes && state.scenes.scenes) || [];
    var ids = scenes.map(function (item) { return item.id; });
    var from = ids.indexOf(fromId);
    var to = ids.indexOf(toId);
    if (from < 0 || to < 0) { return; }
    ids.splice(from, 1);
    ids.splice(to, 0, fromId);
    postJSON("/api/scene/reorder", { scene_ids: ids }).then(function (result) {
      if (!result.ok) {
        toast("\u8c03\u6574\u5931\u8d25\uff1a" + (result.message || ("code " + result.code)));
        return;
      }
      toast("\u5df2\u8c03\u6574\u987a\u5e8f");
      refreshScenes();
    }).catch(function (error) { toast("\u8c03\u6574\u5931\u8d25\uff1a" + error.message); });
  }

  function moveScene(scene, direction, button) {
    button.disabled = true;
    postJSON("/api/scene/move", { scene_id: scene.id, direction: direction })
      .then(function (result) {
        if (!result.ok) {
          toast("\u8c03\u6574\u5931\u8d25\uff1a" + (result.message || ("code " + result.code)));
        }
        refreshScenes();
      })
      .catch(function (error) { toast("\u8c03\u6574\u5931\u8d25\uff1a" + error.message); })
      .then(function () { button.disabled = false; });
  }

  function openSceneEditor(scene) {
    openActionDialog("edit", scene);
  }

  function saveSceneEdit() {
    var draft = draftSpecs();
    if (!draft.length) { toast("\u81f3\u5c11\u7559\u4e00\u4e2a\u52a8\u4f5c"); return; }
    var name = $("timer-name").value.trim();
    if (!name) { toast("\u573a\u666f\u540d\u5b57\u4e0d\u80fd\u4e3a\u7a7a"); return; }
    var button = $("timer-create");
    button.disabled = true;
    var payload = { scene_id: state.editSceneId, name: name, actions: draft };
    if (state.sceneIcon) { payload.icon_url = state.sceneIcon; }
    postJSON("/api/scene/update", payload).then(function (result) {
      if (result.ok) {
        toast("\u5df2\u4fdd\u5b58");
        closeTimerDialog();
        refreshScenes();
      } else {
        toast("\u4fdd\u5b58\u5931\u8d25\uff1a" + (result.message || ("code " + result.code)));
      }
    }).catch(function (error) { toast("\u4fdd\u5b58\u5931\u8d25\uff1a" + error.message); })
      .then(function () { button.disabled = false; });
  }

  function createScene() {
    var device = currentTimerDevice();
    var target = (state.timerTargets || [])[Number($("timer-target").value || 0)];
    if (!device || !target) { toast("\u5148\u9009\u8bbe\u5907\u548c\u52a8\u4f5c"); return; }
    var payload = { name: $("timer-name").value
      || ((device.name || device.model) + " " + target.label) };
    if (state.sceneIcon) { payload.icon_url = state.sceneIcon; }
    var draft = draftSpecs();
    if (draft.length) { payload.actions = draft; }
    else {
      var single = actionPayload(device, target);
      payload.did = single.did;
      payload.kind = single.kind;
      payload.siid = single.siid;
      if (single.piid !== undefined) { payload.piid = single.piid; }
      if (single.aiid !== undefined) { payload.aiid = single.aiid; }
      if (single.value !== undefined) { payload.value = single.value; }
      if (single.inputs !== undefined) { payload.inputs = single.inputs; }
    }
    var button = $("timer-create");
    button.disabled = true;
    postJSON("/api/scene/create", payload).then(function (result) {
      if (result.ok) {
        toast("\u5df2\u521b\u5efa\u573a\u666f");
        closeTimerDialog();
        refreshScenes();
      } else {
        toast("\u521b\u5efa\u5931\u8d25\uff1a" + (result.message || ("code " + result.code)));
      }
    }).catch(function (error) { toast("\u521b\u5efa\u5931\u8d25\uff1a" + error.message); })
      .then(function () { button.disabled = false; });
  }

  // \u4e0b\u62c9\u6846\u7684\u503c\u6c38\u8fdc\u662f\u5b57\u7b26\u4e32\uff0c\u4f46\u8bbe\u5907\u6309\u7c7b\u578b\u6821\u9a8c\uff08\u5199 "4" \u4f1a\u88ab -704220043 \u62d2\uff09
  function typedOptionValue(control, raw) {
    var options = control.options || [];
    for (var i = 0; i < options.length; i += 1) {
      if (String(options[i][0]) === String(raw)) { return options[i][0]; }
    }
    return raw;
  }

  // 定时的改时间 / 删除
  function timerControls(item) {
    var box = el("div", "timer-controls");
    var clock = item.trigger.clock || "";
    if (!clock) {
      var parsed = /(\d{1,2}):(\d{2})/.exec(item.trigger.detail || "");
      if (parsed) { clock = pad2(parsed[1]) + ":" + parsed[2]; }
    }
    var input = el("input", "timer-input");
    input.type = "time";
    input.value = clock;
    input.title = "\u4fee\u6539\u65f6\u95f4\u540e\u70b9\u300c\u4fdd\u5b58\u300d";
    box.appendChild(input);

    var save = el("button", "ghost small", "\u4fdd\u5b58");
    save.addEventListener("click", function () {
      if (!input.value) { toast("\u5148\u9009\u4e00\u4e2a\u65f6\u95f4"); return; }
      var parts = input.value.split(":");
      save.disabled = true;
      postJSON("/api/timer/update", {
        scene_id: item.id, hour: Number(parts[0]), minute: Number(parts[1])
      }).then(function (result) {
        if (result.ok) {
          toast("\u5df2\u6539\u4e3a " + input.value);
          refreshScenes();
        } else {
          toast("\u4fdd\u5b58\u5931\u8d25\uff1a" + (result.message || ("code " + result.code)));
        }
      }).catch(function (error) { toast("\u4fdd\u5b58\u5931\u8d25\uff1a" + error.message); })
        .then(function () { save.disabled = false; });
    });
    box.appendChild(save);

    var remove = el("button", "ghost small danger", "\u5220\u9664");
    remove.addEventListener("click", function () {
      if (!window.confirm("\u5220\u9664\u300c" + item.name + "\u300d\uff1f")) { return; }
      remove.disabled = true;
      postJSON("/api/timer/delete", { scene_id: item.id }).then(function (result) {
        if (result.ok) { toast("\u5df2\u5220\u9664"); refreshScenes(); }
        else { toast("\u5220\u9664\u5931\u8d25\uff1a" + (result.message || ("code " + result.code))); }
      }).catch(function (error) { toast("\u5220\u9664\u5931\u8d25\uff1a" + error.message); })
        .then(function () { remove.disabled = false; });
    });
    box.appendChild(remove);
    return box;
  }

  function pad2(value) {
    var text = String(value);
    return text.length < 2 ? "0" + text : text;
  }

  function refreshScenes() {
    api("/api/scenes?refresh=1").then(function (payload) {
      state.scenes = payload;
      renderScenes(payload);
    }).catch(function () { /* 下一轮再试 */ });
  }

  function toggleScene(item, enable, button) {
    button.disabled = true;
    postJSON("/api/scene/status", { scene_id: item.id, enable: enable })
      .then(function (result) {
        if (result.ok) {
          item.enabled = enable;
          button.className = "switch" + (enable ? " on" : "");
          toast(enable ? "\u5df2\u5f00\u542f\u300c" + item.name + "\u300d"
                       : "\u5df2\u505c\u7528\u300c" + item.name + "\u300d");
        } else {
          toast("\u5207\u6362\u5931\u8d25\uff1a" + (result.message || ("code " + result.code)));
        }
      })
      .catch(function (error) { toast("\u5207\u6362\u5931\u8d25\uff1a" + error.message); })
      .then(function () { button.disabled = false; });
  }

  function runScene(scene, button) {
    button.disabled = true;
    button.textContent = "\u6267\u884c\u4e2d\u2026";
    postJSON("/api/scene/run", { scene_id: scene.id }).then(function (result) {
      if (result.ok) {
        toast("\u5df2\u6267\u884c\u300c" + scene.name + "\u300d");
      } else {
        toast("\u6267\u884c\u5931\u8d25\uff1a" + (result.message || ("code " + result.code)));
      }
    }).catch(function (error) {
      toast("\u6267\u884c\u5931\u8d25\uff1a" + error.message);
    }).then(function () {
      window.setTimeout(function () {
        button.disabled = !scene.enabled;
        button.textContent = scene.enabled ? "\u6267\u884c" : "\u5df2\u7981\u7528";
      }, 1200);
    });
  }

  function roomChip(value, label, count) {
    var chip = el("button", "room-chip" + (state.filterRoom === value ? " active" : ""));
    chip.appendChild(document.createTextNode(label));
    if (count !== undefined && count !== null) {
      chip.appendChild(el("span", "count", count));
    }
    chip.addEventListener("click", function () {
      state.filterRoom = value;
      renderHome();
    });
    return chip;
  }

  function favoriteButton(device) {
    var star = el("button", "fav-star" + (device.pinned ? " on" : ""),
                  device.pinned ? "\u2605" : "\u2606");
    star.type = "button";
    star.title = device.pinned ? "\u53d6\u6d88\u7f6e\u9876" : "\u7f6e\u9876\uff08\u5e38\u7528\u8bbe\u5907\uff09";
    star.addEventListener("click", function (event) {
      event.stopPropagation();
      star.disabled = true;
      postJSON("/api/favorites/toggle", { did: device.did }).then(function (result) {
        if (!result.ok) {
          toast("\u64cd\u4f5c\u5931\u8d25\uff1a" + (result.message || ("code " + result.code)));
          star.disabled = false;
          return;
        }
        device.pinned = result.pinned;
        loadDevices().then(function () {
          if (state.current) { openDevice(state.current); } else { renderHome(); }
        });
      }).catch(function (error) {
        toast("\u64cd\u4f5c\u5931\u8d25\uff1a" + error.message);
        star.disabled = false;
      });
    });
    return star;
  }

  function deviceTile(device) {
    var tile = el("div", "tile" + (device.online ? "" : " offline"));

    var head = el("div", "tile-head");
    var icon = el("img", "tile-icon");
    icon.alt = "";
    if (device.icon) {
      icon.src = device.icon;
      icon.onerror = function () { icon.style.visibility = "hidden"; };
    } else {
      icon.style.visibility = "hidden";
    }
    head.appendChild(icon);

    var titles = el("div", "tile-title");
    var displayName = device.name || device.model || device.did;
    var nameNode = el("div", "tile-name", displayName);
    nameNode.title = displayName;          // 长名字省略时悬停能看全
    titles.appendChild(nameNode);
    var sub = device.room || device.category || "";
    if (!device.online) {
      sub = device.last_seen
        ? (sub ? sub + " \u00b7 " : "") + "\u6700\u540e\u4e0a\u62a5 " + relativeTime(device.last_seen)
        : (sub ? sub + " \u00b7 " : "") + "\u672a\u4e0a\u62a5\u8fc7";
    }
    var subNode = el("div", "tile-sub", sub);
    subNode.title = sub;
    titles.appendChild(subNode);
    head.appendChild(titles);
    head.appendChild(favoriteButton(device));
    tile.appendChild(head);

    var foot = el("div", "tile-foot");
    var primary = device.primary || null;
    var value = primary ? stateValue(device, primary) : null;

    if (primary && primary.widget === "switch") {
      var dot = el("span", "dot" + (device.online ? " on" : ""));
      foot.appendChild(dot);
      var sw = el("button", "switch" + (value ? " on" : ""));
      if (!device.online) { sw.disabled = true; }
      sw.addEventListener("click", function (event) {
        event.stopPropagation();
        quickToggle(device, primary, !value);
      });
      foot.appendChild(sw);
    } else {
      foot.appendChild(el("span", "tile-state" + (value === true ? " on" : ""),
        summaryOf(device, primary, value)));
      if (!primary) {
        foot.appendChild(el("span", "dot" + (device.online ? " on" : "")));
      }
    }
    tile.appendChild(foot);

    tile.addEventListener("click", function () { openDevice(device.did); });
    return tile;
  }

  // 卡片上优先显示"工作状态"这类文字，比裸数值直观
  function summaryOf(device, primary, value) {
    var status = null;
    var list = device.controls || [];
    for (var i = 0; i < list.length; i += 1) {
      if (list[i].name === "status") { status = list[i]; break; }
    }
    var statusValue = status ? stateValue(device, status) : null;
    var statusText = status && statusValue !== null && statusValue !== undefined
      ? describeValue(status, statusValue) : "";
    var primaryText = primary ? describeValue(primary, value) : "";
    // \u8bbe\u5907\u5904\u4e8e\u5f85\u673a/\u5173\u673a\u65f6\uff0c\u201c\u5269\u4f59 546 \u5206\u949f\u201d\u8fd9\u79cd\u6570\u503c\u6ca1\u610f\u4e49\uff0c\u53ea\u663e\u793a\u72b6\u6001\u8bcd
    if (statusText && /\u5173\u673a|\u5f85\u673a|\u79bb\u7ebf|idle|off/i.test(statusText)) {
      return statusText;
    }
    if (statusText && primaryText && statusText !== primaryText) {
      return statusText + " · " + primaryText;
    }
    if (statusText || primaryText) { return statusText || primaryText; }
    return device.online ? "在线" : "离线";
  }

  // 洗衣机这类设备：空闲时 left-time 就等于所选程序的整轮时长
  function cycleMinutes(view) {
    var values = view.state || {};
    var list = (view.controls || []).concat(view.status_list || []);
    var status = null;
    var left = null;
    for (var i = 0; i < list.length; i += 1) {
      if (list[i].name === "status") { status = list[i]; }
      if (list[i].name === "left-time") { left = list[i]; }
    }
    if (!left) { return null; }
    var minutes = values[left.address];
    if (minutes === null || minutes === undefined || Number(minutes) <= 0) { return null; }
    if (status) {
      var statusValue = values[status.address];
      if (statusValue !== null && statusValue !== undefined && Number(statusValue) !== 2) {
        return null;
      }
    }
    return Math.round(Number(minutes));
  }

  function stateValue(device, control) {
    var values = device.state || {};
    var key = control.address;
    return Object.prototype.hasOwnProperty.call(values, key) ? values[key] : null;
  }

  // 离线设备照样能读到值——那是它最后一次上报的。state_seen 是这些值的
  // 上报时间（Unix 秒），用来把「缓存」说成缓存，而不是假装实时。
  function newestSeen(device) {
    var seen = device.state_seen || {};
    var newest = null;
    for (var key in seen) {
      if (!Object.prototype.hasOwnProperty.call(seen, key)) { continue; }
      var stamp = Number(seen[key]);
      if (!stamp) { continue; }
      if (newest === null || stamp > newest) { newest = stamp; }
    }
    if (newest === null) { return null; }
    return Math.max(0, Math.round(Date.now() / 1000 - newest));
  }

  function agoText(seconds) {
    if (seconds < 90) { return "\u521a\u521a"; }
    if (seconds < 3600) { return Math.round(seconds / 60) + "\u0020\u5206\u949f\u524d"; }
    if (seconds < 86400) { return Math.round(seconds / 3600) + "\u0020\u5c0f\u65f6\u524d"; }
    return Math.round(seconds / 86400) + "\u0020\u5929\u524d";
  }

  function describeValue(control, value) {
    if (value === null || value === undefined) {
      // 设备没上报值，但 spec 只声明了一个可能状态时，那它就是答案
      var opts = control.options || [];
      if (opts.length === 1) { return opts[0][1]; }
      return "—";
    }
    if (control.options && control.options.length) {
      for (var i = 0; i < control.options.length; i += 1) {
        if (control.options[i][0] === value) { return control.options[i][1]; }
      }
    }
    var pretty = scaledText(control, value);
    if (pretty) { return pretty; }
    return String(value) + unitText(control);
  }

  function quickToggle(device, control, next) {
    postJSON("/api/set", {
      did: device.did, siid: control.siid, piid: control.piid, value: next
    }).then(function (result) {
      if (result.ok) {
        device.state = result.state || device.state;
        renderHome();
        toast(next ? "已打开" : "已关闭");
      } else {
        toast("失败：" + (result.message || ("code " + result.code)));
      }
    }).catch(function (error) { toast("失败：" + error.message); });
  }

  // ---------- 设备详情 ----------
  function openDevice(did) {
    return api("/api/device?did=" + encodeURIComponent(did)).then(function (payload) {
      state.current = payload;
      $("home-view").hidden = true;
      if ($("scenes-view")) { $("scenes-view").hidden = true; }
      if ($("messages-view")) { $("messages-view").hidden = true; }
      $("detail-view").hidden = false;
      // 详情条已经有返回/名称/在线态，顶部标题栏再显示一次就是重复
      if ($("page-head")) { $("page-head").hidden = true; }
      renderDetail(payload);
      if (window.location.search.indexOf("did=") < 0) {
        window.history.replaceState(null, "", "/?did=" + encodeURIComponent(did));
      }
      startPolling();
    }).catch(function (error) {
      toast("打开设备失败：" + error.message);
    });
  }

  function goHome() {
    stopPolling();
    state.current = null;
    window.history.replaceState(null, "", "/");
    if ($("scenes-view")) { $("scenes-view").hidden = true; }
    if ($("page-head")) { $("page-head").hidden = false; }
    renderHome();
  }

  function renderDetail(view) {
    state.rowUpdaters = {};
    $("detail-breadcrumb").textContent =
      (view.room || "") + (view.room ? " · " : "") + (view.model || "");
    $("device-name").textContent = view.name || view.model || view.did;

    var metaParts = [];
    if (view.model) { metaParts.push(view.model); }
    if (view.category) { metaParts.push(view.category); }
    $("device-meta").textContent = metaParts.join(" · ");

    var icon = $("device-icon");
    if (view.icon) { icon.src = view.icon; icon.hidden = false; }
    else { icon.hidden = true; }

    var online = $("device-online");
    online.textContent = view.online ? "在线" : "离线";
    online.className = "pill small " + (view.online ? "ok" : "off");

    var readonlyToggle = $("readonly-toggle");
    if (readonlyToggle) { readonlyToggle.checked = state.hideReadonly; }

    var note = $("blocked-note");
    var blockedKeys = Object.keys(view.blocked || {});
    if (blockedKeys.length) {
      note.textContent = "有 " + blockedKeys.length + " 个控件当前不可用（下方置灰）";
      note.hidden = false;
    } else {
      note.hidden = true;
    }

    var host = $("groups");
    var side = $("detail-side");
    host.innerHTML = "";
    if (side) { side.innerHTML = ""; }
    setActiveNav("");
    setPage(view.name || view.model || view.did, detailSubtitle(view));

    var actions = view.actions || [];
    var groups = view.groups || [];
    var settable = 0;
    groups.forEach(function (group) { settable += (group.writable || []).length; });
    var hasMain = actions.length > 0 || settable > 0;
    // 只有遥控面板的设备（电视）：面板放主栏正中，别缩在侧栏里
    if (view.remote) {
      (hasMain && side ? side : host).appendChild(remotePanel(view));
    }

    if (actions.length) {
      var actionCard = el("div", "group");
      actionCard.appendChild(el("h3", null, "操作"));
      actions.forEach(function (action) {
        actionCard.appendChild(actionRow(action, view));
      });
      host.appendChild(actionCard);
    }

    var statusList = (view.status_list || []).filter(function (control) {
      return control.widget !== "command";
    });
    if (statusList.length) {
      var statusCard = el("div", "group status-card");
      var statusHead = el("div", "status-head");
      statusHead.appendChild(el("span", "status-badge", "状态"));
      statusHead.appendChild(el("span", "status-title", "设备状态"));
      statusHead.appendChild(el("span", "status-count", statusList.length + " 项"));
      statusCard.appendChild(statusHead);
      var grid = el("div", "status-grid");
      statusList.forEach(function (control) {
        grid.appendChild(controlRow(control, view));
      });
      statusCard.appendChild(grid);
      (side || host).appendChild(statusCard);
    }

    // 遥控面板也是控件：只有遥控器的设备（电视）不要报「规格缺失」
    if (!groups.length && !actions.length && !view.remote) {
      host.appendChild(el("p", "hint", "该设备没有可显示的控件（规格缺失？）"));
      return;
    }
    groups.forEach(function (group) {
      var writable = group.writable || [];
      var readonly = group.readonly || [];
      var hideStatus = state.hideReadonly;
      if (!writable.length) { return; }
      var card = el("div", "group");
      card.appendChild(el("h3", null, group.label));
      writable.forEach(function (control) {
        card.appendChild(controlRow(control, view));
      });
      // 只读项已经全部在「设备状态」卡里，这里不再重复
      host.appendChild(card);
    });

    loadDeviceInfo(view);
    loadDeviceMessages(view);

    var faults = view.fault_notes || [];
    if (faults.length) {
      // 设备正在报故障时直接展开——那时候这张表就是说明书；否则保持收起
      var open = looksFaulting(view) || /[?&]faults=1/.test(window.location.search);
      (side || host).appendChild(faultCard(faults, open));
    }
  }

  // 这台设备的历史消息（洗衣机「洗衣完成」这类）
  function loadDeviceMessages(view) {
    var side = $("detail-side");
    if (!side) { return; }
    var old = $("device-messages-card");
    if (old) { old.parentNode.removeChild(old); }
    api("/api/messages/device?did=" + encodeURIComponent(view.did)).then(function (payload) {
      var messages = (payload && payload.messages) || [];
      if (!messages.length) { return; }
      var card = el("div", "group info-card");
      card.id = "device-messages-card";
      var head = el("h3", null, "\u6d88\u606f\u8bb0\u5f55");
      card.appendChild(head);
      messages.slice(0, 5).forEach(function (item) {
        var row = el("div", "message-row" + (item.is_new ? " unread" : ""));
        var top = el("div", "message-head");
        top.appendChild(el("span", "message-title", item.title));
        row.appendChild(top);
        var bits = [];
        if (item.time) { bits.push(relativeTime(item.time)); }
        if (bits.length) { row.appendChild(el("div", "message-sub", bits.join(" \u00b7 "))); }
        card.appendChild(row);
      });
      side.appendChild(card);
    }).catch(function () { /* 没有历史消息就不显示卡片 */ });
  }

  // 设备信息：型号/固件/序列号/网络/插件文案/最后上报；token 只显示前后几位
  function loadDeviceInfo(view) {
    var side = $("detail-side");
    if (!side) { return; }
    var existing = $("device-info-card");
    if (existing) { existing.parentNode.removeChild(existing); }
    api("/api/device/info?did=" + encodeURIComponent(view.did)).then(function (info) {
      side.appendChild(deviceInfoCard(info));
    }).catch(function () { /* 信息卡拿不到不影响控制 */ });
  }

  function deviceInfoCard(info) {
    var card = el("div", "group info-card");
    card.id = "device-info-card";
    card.appendChild(el("h3", null, "\u8bbe\u5907\u4fe1\u606f"));
    var facts = info.info || {};
    var nameRow = el("div", "meta-row");
    nameRow.appendChild(el("span", "meta-key", "\u540d\u79f0"));
    nameRow.appendChild(el("span", "meta-value", info.name || "\u2014"));
    var rename = el("button", "ghost small info-copy", "\u6539\u540d");
    rename.addEventListener("click", function () { renameDevice(info); });
    nameRow.appendChild(rename);
    card.appendChild(nameRow);
    var rows = [
      ["\u623f\u95f4", info.room || "\u672a\u5206\u914d"],
      ["\u578b\u53f7", info.model || "\u2014"],
      ["\u5382\u5546", facts.manufacturer || "\u2014"],
      ["\u56fa\u4ef6", facts["firmware-revision"] || "\u2014"],
      ["\u5e8f\u5217\u53f7", facts["serial-number"] || "\u2014"],
      ["IP", info.localip || "\u2014"],
      ["MAC", info.mac || "\u2014"],
      ["token", info.token || "\u2014"],
      ["\u63d2\u4ef6\u6587\u6848", (info.plugin && info.plugin.texts)
        ? info.plugin.texts + " \u6761" : "\u65e0"],
      ["\u6700\u540e\u4e0a\u62a5", info.last_report ? relativeTime(info.last_report) : "\u2014"]
    ];
    rows.forEach(function (pair) {
      var row = el("div", "meta-row");
      row.appendChild(el("span", "meta-key", pair[0]));
      var value = el("span", "meta-value", pair[1]);
      if (pair[0] === "\u56fa\u4ef6") {
        value.title = "\u56fa\u4ef6\u7248\u672c\u6765\u81ea\u8bbe\u5907\u81ea\u8eab\uff1b\u5347\u7ea7\u8bf7\u7528\u624b\u673a App\uff08\u672c\u5ba2\u6237\u7aef\u4e0d\u63d0\u4f9b\u5237\u673a\uff09";
      }
      row.appendChild(value);
      card.appendChild(row);
    });
    if ((state.rooms || []).length) {
      var moveRow = el("div", "meta-row");
      moveRow.appendChild(el("span", "meta-key", "\u79fb\u5230"));
      var select = el("select", "meta-select");
      var blank = el("option", null, "\u9009\u623f\u95f4\u2026");
      blank.value = "";
      select.appendChild(blank);
      state.rooms.forEach(function (room) {
        if (room.name === info.room) { return; }
        var option = el("option", null, room.name);
        option.value = room.id;
        select.appendChild(option);
      });
      select.addEventListener("change", function () {
        moveDeviceToRoom(info.did, select.value, select);
      });
      moveRow.appendChild(select);
      card.appendChild(moveRow);
    }

    var note = el("p", "hint",
      "\u56fa\u4ef6\u7248\u672c\u53ea\u8bfb\uff08\u662f\u5426\u6700\u65b0\u672a\u77e5\uff09\uff0c\u5347\u7ea7\u8bf7\u7528\u624b\u673a App\u3002");
    card.appendChild(note);

    var didRow = el("div", "meta-row");
    didRow.appendChild(el("span", "meta-key", "did"));
    var didValue = el("span", "meta-value", info.did);
    didRow.appendChild(didValue);
    var copy = el("button", "ghost small info-copy", "\u590d\u5236");
    copy.addEventListener("click", function () {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(info.did).then(function () { toast("\u5df2\u590d\u5236 did"); },
          function () { toast("\u590d\u5236\u5931\u8d25"); });
      } else {
        window.prompt("\u624b\u52a8\u590d\u5236", info.did);
      }
    });
    didRow.appendChild(copy);
    card.appendChild(didRow);
    return card;
  }

  function detailSubtitle(view) {
    var bits = [];
    if (view.room) { bits.push(view.room); }
    if (view.model) { bits.push(view.model); }
    if (view.category && view.category !== view.room) { bits.push(view.category); }
    return bits.join(" \u00b7 ");
  }

  // 当前状态是不是「故障/异常/报警」（看枚举值的标签，不写死设备）
  function looksFaulting(view) {
    var list = view.controls || [];
    for (var i = 0; i < list.length; i += 1) {
      var options = list[i].options || [];
      var value = stateValue(view, list[i]);
      for (var j = 0; j < options.length; j += 1) {
        if (String(options[j][0]) !== String(value)) { continue; }
        if (/\u6545\u969c|\u5f02\u5e38|\u62a5\u8b66/.test(String(options[j][1]))) {
          return true;
        }
      }
    }
    return false;
  }

  // 故障码对照：内容来自设备插件包（resources.arsc），不是我们编的
  function faultCard(faults, open) {
    var card = document.createElement("details");
    card.open = !!open;
    card.className = "group fault-card";
    var summary = el("summary", "fault-summary");
    summary.appendChild(el("span", "fault-title", "\u6545\u969c\u7801\u5bf9\u7167"));
    summary.appendChild(el("span", "fault-count", faults.length + " \u9879"));
    card.appendChild(summary);
    var list = el("div", "fault-list");
    faults.forEach(function (note) {
      var row = el("div", "fault-row");
      var head = el("div", "fault-head");
      head.appendChild(el("span", "fault-code", note.code));
      head.appendChild(el("span", "fault-name", note.title));
      row.appendChild(head);
      if (note.method) {
        row.appendChild(el("p", "fault-method", note.method));
      }
      list.appendChild(row);
    });
    card.appendChild(list);
    card.appendChild(el("p", "fault-source",
      "\u6765\u6e90\uff1a\u8bbe\u5907\u63d2\u4ef6\u5305\u7684\u5b98\u65b9\u6587\u6848"));
    return card;
  }

  var REMOTE_LABELS = {
    up: "\u25b2", down: "\u25bc", left: "\u25c0", right: "\u25b6",
    ok: "OK", home: "\u4e3b\u9875", back: "\u8fd4\u56de",
    menu: "\u83dc\u5355", settings: "\u8bbe\u7f6e",
    volume_up: "\u97f3\u91cf +", volume_down: "\u97f3\u91cf \u2212",
    power: "\u7535\u6e90", power_on: "\u5f00\u673a",
    play: "\u64ad\u653e", pause: "\u6682\u505c"
  };

  // 内联 SVG：不依赖字体，图标在各浏览器一致
  var ICONS = {
    power: '<path d="M12 3.5v8"/><path d="M6.8 6.6a7.5 7.5 0 1 0 10.4 0"/>',
    home: '<path d="M4 11.2 12 4l8 7.2"/><path d="M6.5 9.8V20h11V9.8"/>',
    back: '<path d="M15 4.5 7.5 12 15 19.5"/>',
    menu: '<path d="M4 7h16"/><path d="M4 12h16"/><path d="M4 17h16"/>',
    settings: '<path d="M4 8h9"/><path d="M17 8h3"/><circle cx="15" cy="8" r="2.2"/>' +
              '<path d="M4 16h5"/><path d="M13 16h7"/><circle cx="11" cy="16" r="2.2"/>',
    play: '<path d="M8 5.5 19 12 8 18.5z" fill="currentColor" stroke="none"/>',
    pause: '<path d="M9 5v14"/><path d="M15 5v14"/>',
    mute: '<path d="M4 9.5h3l4-3.5v12l-4-3.5H4z"/><path d="M16 9l4 6"/><path d="M20 9l-4 6"/>',
    volume: '<path d="M4 9.5h3l4-3.5v12l-4-3.5H4z"/>' +
            '<path d="M15.5 9.6a3.4 3.4 0 0 1 0 4.8"/><path d="M18 7.6a6.6 6.6 0 0 1 0 8.8"/>',
    minus: '<path d="M5 12h14"/>',
    plus: '<path d="M12 5v14"/><path d="M5 12h14"/>',
    chevron: '<path d="M9.5 5.5 16 12l-6.5 6.5" stroke-width="2.2"/>',
    input: '<path d="M4 7h11"/><path d="M4 12h7"/><path d="M4 17h11"/>' +
           '<path d="M17 10l4 4-4 4" fill="none"/>'
  };

  function icon(name, size) {
    var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("width", String(size || 20));
    svg.setAttribute("height", String(size || 20));
    svg.setAttribute("fill", "none");
    svg.setAttribute("stroke", "currentColor");
    svg.setAttribute("stroke-width", "1.9");
    svg.setAttribute("stroke-linecap", "round");
    svg.setAttribute("stroke-linejoin", "round");
    svg.setAttribute("aria-hidden", "true");
    svg.innerHTML = ICONS[name] || "";
    return svg;
  }

  function remoteKey(view, role, className, iconName, text, size) {
    var ref = (view.remote.roles || {})[role];
    if (!ref) { return null; }
    var button = el("button", "rk " + (className || ""));
    if (iconName) { button.appendChild(icon(iconName, size || 20)); }
    if (text) { button.appendChild(el("span", null, text)); }
    button.title = ref.label || REMOTE_LABELS[role] || role;
    if (ref.needs_ble) {
      // 云端没有这条通道：如实禁用，而不是点了报错
      button.classList.add("rk-ble");
      button.disabled = true;
      button.title = "需手机蓝牙，云端无法唤醒";
    }
    button.setAttribute("aria-label", button.title);
    button.addEventListener("click", function () { pressKey(view, role); });
    return button;
  }

  function remotePanel(view) {
    var card = el("div", "group remote-card");
    var head = el("div", "remote-head");
    head.appendChild(el("h3", null, "\u9065\u63a7\u5668"));
    card.appendChild(head);

    var body = el("div", "remote-body");
    var roles = view.remote.roles || {};

    // ── 顶部：电源键 ─────────────────────────────
    var top = el("div", "remote-top");
    var power = remoteKey(view, "power", "rk-power", "power", "", 20);
    if (power) { top.appendChild(power); }
    var powerOn = remoteKey(view, "power_on", "rk-power-on", null, "\u5f00\u673a");
    if (powerOn) { top.appendChild(powerOn); }
    // 电视的 turn-on 走 BLE，云端到不了；账号里有小爱音箱时可以借它执行
    if (powerOn && powerOn.disabled && view.wake_via) {
      var wake = el("button", "rk rk-wake", "\u5c0f\u7231\u5524\u9192");
      wake.title = "\u901a\u8fc7" + view.wake_via.name + "\u6267\u884c\u201c\u6253\u5f00\u7535\u89c6\u201d";
      wake.addEventListener("click", function () { wakeTV(view, wake); });
      top.appendChild(wake);
    } else if (powerOn && powerOn.disabled && view.mac) {
      // 没有音箱时的兜底：Wake-on-LAN（需电视设置里开启「网络唤醒」）
      var wol = el("button", "rk rk-wake", "\u7f51\u7edc\u5524\u9192");
      wol.title = "\u53d1\u9001 WOL \u5e7f\u64ad\u5230 " + view.mac + "\uff08\u9700\u7535\u89c6\u8bbe\u7f6e\u91cc\u5f00\u542f\u300c\u7f51\u7edc\u5524\u9192\u300d\uff09";
      wol.addEventListener("click", function () { wakeLAN(view, wol); });
      top.appendChild(wol);
    }
    body.appendChild(top);

    // ── 圆形方向盘 ───────────────────────────────
    var dpad = el("div", "dpad");
    dpad.appendChild(el("span", "dpad-ring"));
    var dirs = [["up", "up"], ["right", "right"], ["down", "down"], ["left", "left"]];
    dirs.forEach(function (pair) {
      var role = pair[0];
      if (!roles[role]) { return; }
      var button = el("button", "dp dp-" + role);
      button.appendChild(icon("chevron", 22));
      button.title = (roles[role] || {}).label || role;
      button.setAttribute("aria-label", button.title);
      button.addEventListener("click", function () { pressKey(view, role); });
      dpad.appendChild(button);
    });
    if (roles.ok) {
      var ok = el("button", "dp-ok", "OK");
      ok.title = (roles.ok || {}).label || "OK";
      ok.addEventListener("click", function () { pressKey(view, "ok"); });
      dpad.appendChild(ok);
    }
    body.appendChild(dpad);

    // ── 导航键：主页 / 返回 / 菜单 / 设置 ─────────
    var nav = el("div", "remote-nav");
    [["home", "home"], ["back", "back"], ["menu", "menu"],
     ["settings", "settings"]].forEach(function (pair) {
      var button = remoteKey(view, pair[0], "rk-nav", pair[1], "", 19);
      if (button) { nav.appendChild(button); }
    });
    body.appendChild(nav);

    // ── 音量摇杆 + 静音 ──────────────────────────
    var rocker = el("div", "rocker");
    var down = remoteKey(view, "volume_down", "rk-rocker", "minus", "", 18);
    if (down) { rocker.appendChild(down); }
    var muteControl = null;
    var volumeProps = view.remote.properties || {};
    if (volumeProps.mute) {
      muteControl = findControl(view, volumeProps.mute);
      if (muteControl) {
        var muted = stateValue(view, muteControl);
        var mute = el("button", "rk rk-rocker rk-mute" + (muted ? " on" : ""));
        mute.appendChild(icon(muted ? "mute" : "volume", 18));
        mute.title = muted ? "\u53d6\u6d88\u9759\u97f3" : "\u9759\u97f3";
        mute.setAttribute("aria-label", mute.title);
        mute.addEventListener("click", function () {
          // 读当下状态再取反：闭包里的 muted 可能是上一轮轮询的旧值
          sendSet(muteControl, !stateValue(view, muteControl), view);
        });
        rocker.appendChild(mute);
      }
    }
    var up = remoteKey(view, "volume_up", "rk-rocker", "plus", "", 18);
    if (up) { rocker.appendChild(up); }
    body.appendChild(rocker);

    // 当前音量：以前这块永远是空的，因为遥控面板上的属性被从 controls 里删掉了，
    // 既读不到值也找不到控件（2026-11-21 修）。
    if (volumeProps.volume) {
      var volumeControl = findControl(view, volumeProps.volume);
      if (volumeControl) {
        var level = stateValue(view, volumeControl);
        var readout = el("div", "rocker-readout");
        readout.appendChild(el("span", null, "\u97f3\u91cf"));
        readout.appendChild(el("b", null, level === null || level === undefined
          ? "\u2014"
          : String(level) + (volumeControl.unit_label || "")));
        readout.title = "\u8bbe\u5907\u4e0a\u62a5\u7684\u5f53\u524d\u97f3\u91cf";
        body.appendChild(readout);
      }
    }

    // ── 播放 / 暂停 ──────────────────────────────
    var media = el("div", "remote-media");
    var play = remoteKey(view, "play", "rk-media", "play", "", 20);
    if (play) { media.appendChild(play); }
    var pause = remoteKey(view, "pause", "rk-media", "pause", "", 20);
    if (pause) { media.appendChild(pause); }
    body.appendChild(media);

    // ── 输入源 ───────────────────────────────────
    if (volumeProps["input-source"]) {
      var inputControl = findControl(view, volumeProps["input-source"]);
      if (inputControl) {
        var footer = el("div", "remote-input");
        footer.appendChild(icon("input", 16));
        footer.appendChild(buildWidget(inputControl,
          stateValue(view, inputControl), !inputControl.writable, view));
        body.appendChild(footer);
      }
    }

    if (!view.online) {
      var note = el("p", "remote-offline", "\u8bbe\u5907\u79bb\u7ebf\uff0c\u6309\u952e\u4e0d\u4f1a\u54cd\u5e94");
      var age = newestSeen(view);
      if (age !== null) {
        note.textContent += "\uff08\u4e0b\u9762\u663e\u793a\u7684\u662f " + agoText(age) + "\u4e0a\u62a5\u7684\u503c\uff09";
      }
      body.appendChild(note);
      var tip = el("p", "remote-offline",
        "\u5c0f\u63d0\u793a\uff1a\u628a\u9065\u63a7\u5668\u77ed\u6309\u6539\u4e3a\u300c\u606f\u5c4f\u300d\uff0c\u5524\u9192\u53ea\u9700 1-2 \u79d2");
      body.appendChild(tip);
    }

    card.appendChild(body);
    return card;
  }

  function findControl(view, ref) {
    var list = view.controls || [];
    for (var i = 0; i < list.length; i += 1) {
      if (list[i].siid === ref.siid && list[i].piid === ref.piid) { return list[i]; }
    }
    var options = ref.options || [];
    return { siid: ref.siid, piid: ref.piid, writable: !!ref.writable,
             options: options,
             widget: options.length ? "select" : "switch",
             label: "\u8f93\u5165\u6e90", name: "input-source",
             address: ref.siid + "." + ref.piid };
  }

  // 唤醒后分两段等：先等它回到局域网（秒级），再等云端状态跟上
  function wakeLAN(view, button) {
    button.disabled = true;
    postJSON("/api/wol", { did: view.did }).then(function (result) {
      button.disabled = false;
      if (result.ok) {
        toast("\u5df2\u53d1\u9001\u7f51\u7edc\u5524\u9192\uff08" + result.mac + "\uff09");
        waitForBoot(view);
      } else {
        toast("\u5931\u8d25\uff1a" + (result.message || "\u672a\u53d1\u9001"));
      }
    }).catch(function (error) {
      button.disabled = false;
      toast("\u5931\u8d25\uff1a" + error.message);
    });
  }

  // 只把新数值塞进已有控件，不重建 DOM
  function applyState(view, nextState) {
    if (!nextState) { return; }
    view.state = nextState;
    Object.keys(state.rowUpdaters).forEach(function (address) {
      var value = Object.prototype.hasOwnProperty.call(nextState, address)
        ? nextState[address] : null;
      try {
        state.rowUpdaters[address](value);
      } catch (e) { /* 控件已随页面切走 */ }
    });
  }

  function waitForBoot(view) {
    var started = Date.now();
    var timer = window.setInterval(function () {
      if (Date.now() - started > 180000) {
        window.clearInterval(timer);
        toast("\u7b49\u5f85\u8d85\u65f6\uff0c\u8bf7\u624b\u52a8\u5237\u65b0");
        return;
      }
      api("/api/presence?did=" + encodeURIComponent(view.did)).then(function (p) {
        if (p.checked && p.reachable) {
          window.clearInterval(timer);
          toast("\u7535\u89c6\u5df2\u8054\u7f51\uff08" + Math.round((Date.now() - started) / 1000) + "s\uff09");
          window.setTimeout(function () { openDevice(view.did); }, 2500);
        }
      }).catch(function () { /* 探测失败继续等 */ });
    }, 2000);
  }

  function wakeTV(view, button) {
    button.disabled = true;
    toast("\u6b63\u5728\u901a\u8fc7\u5c0f\u7231\u5524\u9192\u2026");
    postJSON("/api/wake-tv", { did: view.did }).then(function (result) {
      button.disabled = false;
      if (result.ok) {
        toast("\u5df2\u53d1\u9001\u5230\u300c" + result.speaker + "\u300d\uff0c\u7b49\u5f85\u7535\u89c6\u5f00\u673a");
        waitForBoot(view);
      } else {
        toast("\u5931\u8d25\uff1a" + (result.message || ("code " + result.code)));
      }
    }).catch(function (error) {
      button.disabled = false;
      toast("\u5931\u8d25\uff1a" + error.message);
    });
  }

  function pressKey(view, role) {
    var ref = (view.remote.roles || {})[role];
    if (!ref) { return; }
    if (!view.online) { toast("\u8bbe\u5907\u79bb\u7ebf"); return; }
    postJSON("/api/action", {
      did: view.did, siid: ref.siid, aiid: ref.aiid
    }).then(function (result) {
      if (!result.ok) { toast("\u5931\u8d25\uff1a" + (result.message || ("code " + result.code))); }
    }).catch(function (error) { toast("\u5931\u8d25\uff1a" + error.message); });
  }

  // inline 参数要用户填；引用参数不需要界面
  function actionInputs(action) {
    return (action.params || []).filter(function (param) {
      return param.inline;
    });
  }

  function paramInput(param) {
    var holder = el("label", "param-field");
    holder.appendChild(el("span", "param-label",
      param.label || ("参数 " + param.piid)));
    var field;
    if (param.value_list && param.value_list.length) {
      field = el("select", "param-input");
      param.value_list.forEach(function (option) {
        var item = el("option", "", option[2] || option[1] || option[0]);
        item.value = String(option[0]);
        field.appendChild(item);
      });
      field.value = String(param.value_list[0][0]);
    } else if (param.format === "bool") {
      field = el("input", "param-input");
      field.type = "checkbox";
    } else if (param.format === "string") {
      field = el("input", "param-input");
      field.type = "text";
    } else {
      field = el("input", "param-input");
      field.type = "number";
      field.step = param.format === "float" ? "0.1" : "1";
      if (param.value_range) {
        field.min = String(param.value_range[0]);
        field.max = String(param.value_range[1]);
      }
      field.placeholder = param.unit ? ("单位 " + param.unit) : "";
    }
    field.setAttribute("data-piid", String(param.piid));
    holder.appendChild(field);
    return holder;
  }

  function actionRow(action, view) {
    var row = el("div", "row");
    var labelBox = el("div", "row-label");
    labelBox.appendChild(el("div", "name", action.label || action.name));
    var inline = actionInputs(action);
    if (inline.length) {
      labelBox.appendChild(el("div", "tile-sub",
        "需要 " + inline.length + " 个参数"));
    }
    row.appendChild(labelBox);
    var holder = el("div", "row-control");
    inline.forEach(function (param) { holder.appendChild(paramInput(param)); });
    var button = el("button", "chip", "执行");
    button.addEventListener("click", function () {
      var values = {};
      var fields = holder.querySelectorAll("input,select");
      for (var i = 0; i < fields.length; i += 1) {
        var field = fields[i];
        values[field.getAttribute("data-piid")] = field.type === "checkbox"
          ? field.checked : field.value;
      }
      runAction(action, view, values);
    });
    holder.appendChild(button);
    row.appendChild(holder);
    return row;
  }

  function runAction(action, view, inputs) {
    toast("执行中…");
    postJSON("/api/action", {
      did: view.did, siid: action.siid, aiid: action.aiid,
      inputs: inputs || {}
    }).then(function (result) {
      if (result.ok) {
        toast("已执行");
      // 场景会改设备状态，回列表时不要显示旧值
      refreshAfterScene();
        window.setTimeout(function () { openDevice(view.did); }, 1200);
      } else {
        toast("失败：" + (result.message || ("code " + result.code)));
      }
    }).catch(function (error) { toast("失败：" + error.message); });
  }

  // ---------- 控件 ----------
  function controlRow(control, view) {
    var row = el("div", "row");
    var value = stateValue(view, control);
    var why = (view.blocked || {})[control.address] || "";
    var locked = !control.writable || !!why;
    if (why) { row.className += " disabled"; }

    var labelBox = el("div", "row-label");
    var nameLine = el("div", "name");
    nameLine.appendChild(document.createTextNode(control.label || control.name));
    if (!control.writable) {
      nameLine.appendChild(el("span", "badge", "只读"));
    }
    labelBox.appendChild(nameLine);
    if (control.name === "mode") {
      var cycle = cycleMinutes(view);
      if (cycle) {
        labelBox.appendChild(el("div", "cycle-hint", "本轮约 " + cycle + " 分钟"));
      }
    }
    if (why) { labelBox.appendChild(el("div", "why", why)); }
    row.appendChild(labelBox);

    var holder = el("div", "row-control");
    holder.appendChild(buildWidget(control, value, locked, view));
    row.appendChild(holder);

    // 轮询只换这一个 holder 的内容，不重建整页：否则长页面每 5 秒闪一次、
    // 滚动位置还会跳。
    state.rowUpdaters[control.address] = function (next) {
      // 用户正在这一格里操作（比如展开了下拉）时绝不能重建 DOM：
      // 原生 <select> 的弹出层属于浏览器 UI，节点被替换就会自动收起。
      if (state.interacting) { return; }
      var active = document.activeElement;
      if (active && holder.contains(active)) { return; }

      var select = holder.querySelector("select");
      if (select) {
        var wanted = next === null || next === undefined ? "" : String(next);
        for (var i = 0; i < select.options.length; i += 1) {
          if (select.options[i].value === wanted) {
            if (select.value !== wanted) { select.value = wanted; }
            return;                      // 只改选中项，不重建
          }
        }
        // 值不在枚举表里（设备报了个新状态）才重建，把「当前值」补进去
      }
      holder.innerHTML = "";
      holder.appendChild(buildWidget(control, next, locked, view));
    };
    row.setAttribute("data-addr", control.address);
    return row;
  }

  function unitText(control) {
    // unit_label 是后端解析过的展示单位；为空就表示「不显示单位」
    // （例如 0..65535 的原始亮度），绝不能回退成 spec 里的英文 unit。
    var label = control.unit_label || "";
    return label ? " " + label : "";
  }

  // 把原始值换算成「自然数值」，例如灯泡亮度 1..65535 -> 0..100%
  function scaledText(control, value) {
    var scale = control.scale;
    if (!scale || value === null || value === undefined) { return null; }
    if (scale.type === "percent") {
      var span = (scale.max - scale.min) || 1;
      var pct = Math.round(((value - scale.min) / span) * 100);
      if (pct < 0) { pct = 0; }
      if (pct > 100) { pct = 100; }
      return pct + "%";
    }
    return null;
  }

  // 秒数 -> HH:MM（饮水机"勿扰时间段"就是这种）
  function timeOfDayText(value) {
    if (value === null || value === undefined) { return "—"; }
    var total = Math.round(Number(value)) % 86400;
    if (total < 0) { total += 86400; }
    var h = Math.floor(total / 3600);
    var m = Math.floor((total % 3600) / 60);
    return (h < 10 ? "0" : "") + h + ":" + (m < 10 ? "0" : "") + m;
  }

  function boolText(value) {
    if (value === true) { return "是"; }
    if (value === false) { return "否"; }
    return "—";
  }

  function buildWidget(control, value, locked, view) {
    var widget = control.widget;

    if (widget === "state") {
      var isOn = value === true;
      return el("span", "state-pill" + (isOn ? " on" : ""), boolText(value));
    }

    if (widget === "progress") {
      var rng = control.range || [0, 100, 1];
      var span = (rng[1] - rng[0]) || 1;
      var ratio = value === null || value === undefined ? 0 : (Number(value) - rng[0]) / span;
      if (ratio < 0) { ratio = 0; }
      if (ratio > 1) { ratio = 1; }
      var wrapP = el("div", "progress-wrap");
      var bar = el("div", "progress-bar");
      var fill = el("div", "progress-fill");
      fill.style.width = Math.round(ratio * 100) + "%";
      bar.appendChild(fill);
      wrapP.appendChild(bar);
      var text = control.time_of_day ? timeOfDayText(value)
        : (value === null || value === undefined ? "—" : String(value) + unitText(control));
      wrapP.appendChild(el("span", "progress-text", text));
      return wrapP;
    }

    if (widget === "value") {
      if (control.time_of_day) { return el("span", "readonly", timeOfDayText(value)); }
      if (value === null || value === undefined) { return el("span", "readonly", "—"); }
      return el("span", "readonly", describeValue(control, value));
    }

    if (widget === "command") {
      var run = el("button", "chip", "执行");
      run.disabled = locked;
      run.title = control.name;
      run.addEventListener("click", function () {
        sendSet(control, control.command_value, view);
      });
      return run;
    }

    if (widget === "switch") {
      var button = el("button", "switch" + (value ? " on" : ""));
      button.disabled = locked;
      button.addEventListener("click", function () { sendSet(control, !value, view); });
      return button;
    }

    if (widget === "select" || widget === "buttons") {
      var options = (control.options || []).slice();
      var known = false;
      for (var i = 0; i < options.length; i += 1) {
        if (options[i][0] === value) { known = true; break; }
      }
      if (!known && value !== null && value !== undefined) {
        // 设备报了一个不在枚举表里的值：照样显示出来，别让用户以为坏了
        options.unshift([value, "当前值 " + value]);
      }
      if (widget === "select") {
        var select = document.createElement("select");
        select.disabled = locked;
        options.forEach(function (pair) {
          var option = document.createElement("option");
          option.value = String(pair[0]);
          option.textContent = pair[1];
          if (pair[0] === value) { option.selected = true; }
          select.appendChild(option);
        });
        select.addEventListener("change", function () {
          // 用选项自带的原始类型发送：<option> 的 value 永远是字符串，
          // 直接送 "4" 会被设备当成类型错误拒绝（-704220043）。
          var raw = select.value;
          for (var k = 0; k < options.length; k += 1) {
            if (String(options[k][0]) === select.value) { raw = options[k][0]; break; }
          }
          if (raw === select.value) { raw = coerceLike(value, select.value); }
          sendSet(control, raw, view);
        });
        return select;
      }
      var group = el("div", "buttons");
      options.forEach(function (pair) {
        var chip = el("button", "chip" + (pair[0] === value ? " on" : ""), pair[1]);
        chip.disabled = locked;
        chip.addEventListener("click", function () { sendSet(control, pair[0], view); });
        group.appendChild(chip);
      });
      return group;
    }

    if (widget === "slider") {
      var range = control.range || [0, 100, 1];
      var wrap = el("div", "slider-wrap");
      var input = document.createElement("input");
      input.type = "range";
      input.min = String(range[0]);
      input.max = String(range[1]);
      input.step = String(range[2] || 1);
      input.value = String(value === null || value === undefined ? range[0] : value);
      input.disabled = locked;
      var format = function (raw) {
        if (control.time_of_day) { return timeOfDayText(Number(raw)); }
        return scaledText(control, Number(raw)) || (raw + unitText(control));
      };
      var label = el("span", "value", format(input.value));
      input.addEventListener("input", function () { label.textContent = format(input.value); });
      input.addEventListener("change", function () {
        sendSet(control, Number(input.value), view);
      });
      wrap.appendChild(input);
      wrap.appendChild(label);
      return wrap;
    }

    if (widget === "stepper") {
      var box = document.createElement("input");
      box.type = "number";
      box.value = String(value === null || value === undefined ? "" : value);
      if (control.range) {
        box.min = String(control.range[0]);
        box.max = String(control.range[1]);
        box.step = String(control.range[2] || 1);
      }
      box.disabled = locked;
      box.addEventListener("change", function () {
        sendSet(control, Number(box.value), view);
      });
      return box;
    }

    if (widget === "text") {
      if (locked) {
        return el("span", "readonly", value === null ? "—" : String(value));
      }
      var text = document.createElement("input");
      text.type = "text";
      text.value = value === null || value === undefined ? "" : String(value);
      text.addEventListener("change", function () { sendSet(control, text.value, view); });
      return text;
    }

    var pretty = scaledText(control, value);
    if (pretty) { return el("span", "readonly", pretty); }
    return el("span", "readonly", describeValue(control, value));
  }

  function coerceLike(previous, raw) {
    if (typeof previous === "number") {
      var parsed = Number(raw);
      return isNaN(parsed) ? previous : parsed;
    }
    return raw;
  }

  // ---------- 写入 ----------
  function sendSet(control, value, view) {
    toast("发送中…");
    window.setTimeout(loadHomeStatus, 2500);
    postJSON("/api/set", {
      did: view.did, siid: control.siid, piid: control.piid, value: value
    }).then(function (result) {
      if (result.ok) {
        applyState(view, result.state || view.state);
        fastPoll(view);
        toast("已发送");
        // 改了温度等会改变另一个控件的上限（空气炸锅：温度→时间），
        // 所以要拿一次新载荷把 range/hint 也刷新掉
        window.setTimeout(function () { openDevice(view.did); }, 500);
      } else {
        toast("失败：" + (result.message || ("code " + result.code)));
      }
    }).catch(function (error) { toast("失败：" + error.message); });
  }

  // ---------- 轮询 ----------
  // 刚写完状态后密集刷几次，置灰立刻跟上，不忆等 5 秒
  function fastPoll(view) {
    var rounds = 0;
    var timer = window.setInterval(function () {
      rounds += 1;
      if (rounds > 6 || !state.current || state.current.did !== view.did) {
        window.clearInterval(timer);
        return;
      }
      postJSON("/api/refresh", { did: view.did }).then(function (result) {
        if (result.state) { applyState(view, result.state); }
      }).catch(function () { /* 忽略瞬时失败 */ });
    }, 1200);
  }

  // 拉一次当前设备状态；轮询和「窗口重新获得焦点」都走这里
  function refreshOnce(did) {
    var target = did || (state.current && state.current.did);
    if (!target) { return Promise.resolve(); }
    return postJSON("/api/refresh", { did: target }).then(function (result) {
      if (!state.current || state.current.did !== target || !result.state) {
        return;
      }
      applyState(state.current, result.state);
    }).catch(function () { /* 轮询失败静默 */ });
  }

  function startPolling() {
    stopPolling();
    if (!state.status || state.status.demo) { return; }
    state.pollTimer = window.setInterval(function () {
      if (!state.current || state.interacting) { return; }
      refreshOnce();
    }, 5000);
  }

  function stopPolling() {
    if (state.pollTimer) {
      window.clearInterval(state.pollTimer);
      state.pollTimer = null;
    }
  }

  // ---------- 扫码登录 ----------
  function openLogin() {
    $("login-modal").hidden = false;
    $("login-title").textContent = "扫码登录";
    $("login-subtitle").textContent = "用米家 App 扫描二维码";
    $("login-msg").className = "meta";
    $("login-msg").textContent = "正在获取二维码…";
    $("qr-wrap").hidden = false;
    $("qr-image").hidden = true;
    $("qr-spinner").hidden = false;
    $("login-loading").hidden = true;
    $("login-hint").hidden = false;
    $("login-retry").hidden = true;
    api("/api/qr").catch(function (error) {
      $("login-msg").textContent = "获取失败：" + error.message;
    });
    if (state.qrTimer) { window.clearInterval(state.qrTimer); }
    state.qrTimer = window.setInterval(pollLogin, 1500);
  }

  function showLoginLoading(message) {
    $("login-modal").hidden = false;
    $("login-title").textContent = "永远相信美好的事情即将发生";
    $("login-subtitle").textContent = "正在连接账号并同步你的米家设备";
    $("qr-wrap").hidden = true;
    $("login-loading").hidden = false;
    $("login-hint").hidden = true;
    $("login-retry").hidden = true;
    $("login-msg").className = "meta";
    $("login-msg").textContent = message;
  }

  function showLoginError(message) {
    $("login-modal").hidden = false;
    $("login-title").textContent = "永远相信美好的事情即将发生";
    $("login-subtitle").textContent = "设备数据还没有加载完成";
    $("qr-wrap").hidden = true;
    $("login-loading").hidden = true;
    $("login-hint").hidden = true;
    $("login-retry").hidden = false;
    $("login-msg").className = "meta login-error";
    $("login-msg").textContent = "加载失败：" + message;
  }

  function pollLogin() {
    api("/api/qr/status").then(function (payload) {
      if (payload.state === "waiting") {
        if (payload.has_image && $("qr-image").hidden) {
          $("qr-image").src = "/api/qr/image?t=" + Date.now();
          $("qr-image").hidden = false;
          $("qr-spinner").hidden = true;
          $("login-msg").textContent = "用米家 App 扫描上面的二维码";
        }
        return;
      }
      if (payload.state === "ok") {
        window.clearInterval(state.qrTimer);
        state.qrTimer = null;
        showLoginLoading("登录成功，正在读取账号数据…");
        verifyLogin(0);
        return;
      }
      if (payload.state === "error") {
        window.clearInterval(state.qrTimer);
        state.qrTimer = null;
        $("login-msg").textContent = "失败：" + payload.message;
      }
    }).catch(function () { /* 忽略瞬时错误 */ });
  }

  // 扫码确认后，先等 provider 切到账号，再等设备列表真正回来。
  // 加载期间不关掉弹窗；失败必须给出原因和重试入口，不能只留一个空白首页。
  function verifyLogin(attempt) {
    showLoginLoading(attempt ? "仍在等待账号状态，正在重试…" : "登录成功，正在读取账号数据…");
    loadStatus().catch(function () { return null; }).then(function (status) {
      if (status && !status.demo) {
        showLoginLoading("账号已连接，正在加载设备列表…");
        boot().then(function (result) {
          if (result && result.devicesError) {
            showLoginError(result.devicesError.message || String(result.devicesError));
            return;
          }
          $("login-modal").hidden = true;
          toast("设备数据已更新");
        });
        return;
      }
      if (attempt < 5) {
        window.setTimeout(function () { verifyLogin(attempt + 1); }, 600);
        return;
      }
      showLoginError(status && status.credentials_error
        ? status.credentials_error
        : "登录状态尚未生效，请重试；若持续失败，请重新扫码");
    });
  }

  function closeLogin() {
    $("login-modal").hidden = true;
    if (state.qrTimer) {
      window.clearInterval(state.qrTimer);
      state.qrTimer = null;
    }
  }

  // ---------- 启动 ----------
  function boot() {
    stopPolling();
    state.current = null;
    var devicesError = null;
    // 分开兜底：/api/status 成功但设备列表失败（云端慢、被墙、token 过期）
    // 时，页面仍要完成启动 —— 否则连深链接和账号面板都打不开。
    return loadStatus().catch(function (error) {
      toast("状态读取失败：" + error.message);
      return null;
    }).then(function () {
      return loadDevices().catch(function (error) {
        devicesError = error;
        toast("设备列表加载失败：" + error.message);
        state.devices = [];
        state.rooms = [];
        state.home = "";
        renderHome();
      });
    }).then(function () {
      refreshBadges();
      startUnreadPolling();
      openFromLocation();
      return { devicesError: devicesError };
    }).catch(function (error) {
      toast("加载失败：" + error.message);
      return { devicesError: error };
    });
  }

  // 深链接：/?did=xxx 直接打开某台设备
  function openFromLocation() {
    if (/[?&]status=1/.test(window.location.search)) {
      window.setTimeout(openStatus, 400);
    }
    var scrollTo = /[?&]scroll=(\d+)/.exec(window.location.search);
    if (scrollTo) {
      // 调试/截图用：打开后直接滚到某个位置
      window.setTimeout(function () { window.scrollTo(0, Number(scrollTo[1])); }, 5000);
    }
    // 深链接：/?room=客厅 直接筛某个房间
    var roomMatch = /[?&]room=([^&]+)/.exec(window.location.search);
    if (roomMatch) {
      state.filterRoom = decodeURIComponent(roomMatch[1]);
      renderHome();
    }

    // 深链接：/?view=account 直接打开「账号与同步」
    if (/[?&]view=account/.test(window.location.search)) {
      window.setTimeout(openAccount, 200);
      return;
    }

    // 深链接：/?view=scenes 打开场景页（?newtimer=1 顺带开弹窗）、/?view=messages 打开消息
    if (/[?&]view=messages/.test(window.location.search)) {
      window.setTimeout(openMessages, 200);
      return;
    }
    if (/[?&]view=scenes/.test(window.location.search)) {
      window.setTimeout(function () {
        openScenes();
        if (/[?&]newtimer=1/.test(window.location.search)) {
          window.setTimeout(function () { openActionDialog("timer"); }, 600);
        }
        if (/[?&]newscene=1/.test(window.location.search)) {
          window.setTimeout(function () { openActionDialog("scene"); }, 600);
        }
      }, 200);
      return;
    }
    var match = /[?&]did=([^&]+)/.exec(window.location.search);
    if (!match) { return; }
    var did = decodeURIComponent(match[1]);
    var exists = state.devices.some(function (device) { return device.did === did; });
    if (exists) { openDevice(did); }
  }

  function bind() {
    $("refresh-btn") && $("refresh-btn").addEventListener("click", function () {
      loadStatus().then(loadDevices).then(function () { toast("已刷新"); });
    });
    $("theme-btn").addEventListener("click", toggleTheme);
    var ro = $("readonly-toggle");
    if (ro) {
      ro.addEventListener("change", function () {
        state.hideReadonly = ro.checked;
        try { window.localStorage.setItem("mihome-hide-readonly", ro.checked ? "1" : "0"); }
        catch (e) { /* 隐私模式 */ }
        if (state.current) { renderDetail(state.current); }
      });
    }
    $("back-btn").addEventListener("click", goHome);
    $("login-btn").addEventListener("click", openLogin);
    $("status-btn").addEventListener("click", openStatus);

    // 侧栏导航
    Array.prototype.forEach.call(document.querySelectorAll("[data-nav]"), function (item) {
      item.addEventListener("click", function () {
        var which = item.getAttribute("data-nav");
        if (which === "home") { goHome(); }
        else if (which === "scenes") { openScenes(); }
        else if (which === "messages") { openMessages(); }
        else if (which === "status") { openStatus(); }
        else if (which === "account") { openAccount(); }
      });
    });

    var search = $("search-input");
    if (search) {
      search.addEventListener("input", function () {
        state.query = search.value || "";
        if (!$("home-view").hidden) { renderHome(); }
      });
    }
    $("new-timer-btn") && $("new-timer-btn").addEventListener("click", function () {
      openActionDialog("timer");
    });
    $("new-scene-btn") && $("new-scene-btn").addEventListener("click", function () {
      openActionDialog("scene");
    });
    $("message-read-btn") && $("message-read-btn").addEventListener("click", markRead);
    if ($("home-name")) {
    $("home-name").title = "\u70b9\u51fb\u6539\u5bb6\u5ead\u540d";
    $("home-name").style.cursor = "pointer";
    $("home-name").addEventListener("click", renameHome);
    }
    $("timer-cancel") && $("timer-cancel").addEventListener("click", closeTimerDialog);
    $("timer-create") && $("timer-create").addEventListener("click", createTimer);
    $("timer-device") && $("timer-device").addEventListener("change", fillTimerTargets);
    $("timer-target") && $("timer-target").addEventListener("change", fillTimerValue);
    $("timer-add-action") && $("timer-add-action").addEventListener("click", addDraftAction);
    $("timer-repeat") && $("timer-repeat").addEventListener("change", function () {
      var row = $("timer-days-row");
      if (row) { row.hidden = $("timer-repeat").value !== "weekly"; }
    });
    $("timer-modal") && $("timer-modal").addEventListener("click", function (event) {
      if (event.target === $("timer-modal")) { closeTimerDialog(); }
    });
    $("close-account") && $("close-account").addEventListener("click", function () {
      $("account-modal").hidden = true;
    });
    $("account-modal") && $("account-modal").addEventListener("click", function (event) {
      if (event.target === $("account-modal")) { $("account-modal").hidden = true; }
    });
    $("close-status").addEventListener("click", function () {
      $("status-modal").hidden = true;
    });
    $("status-modal").addEventListener("click", function (event) {
      if (event.target === $("status-modal")) { $("status-modal").hidden = true; }
    });
    $("close-modal").addEventListener("click", closeLogin);
    $("login-retry").addEventListener("click", function () { verifyLogin(0); });
    $("login-modal").addEventListener("click", function (event) {
      if (event.target === $("login-modal")) { closeLogin(); }
    });
    // 光标进入控件区（展开下拉、拖滑块、输入数字）时暂停轮询，
    // 离开后再恢复，避免「选到一半自己收起来」。
    var detail = $("detail-view");
    detail.addEventListener("focusin", function () { state.interacting = true; });
    detail.addEventListener("pointerdown", function () { state.interacting = true; });
    detail.addEventListener("focusout", function () {
      window.setTimeout(function () {
        var active = document.activeElement;
        var inside = active && detail.contains(active);
        if (!inside) { state.interacting = false; }
      }, 400);
    });
    window.addEventListener("pointerup", function () {
      window.setTimeout(function () {
        var active = document.activeElement;
        var inside = active && detail.contains(active);
        if (!inside) { state.interacting = false; }
      }, 600);
    });

    window.addEventListener("scroll", function () {
      var head = $("detail-fixed");
      if (!head) { return; }
      head.classList.toggle("is-stuck", window.scrollY > 8);
    }, { passive: true });

    // 切回窗口时立刻同步一次：轮询 5 秒一次，回来时往往已经过期了。
    // 也顺手核对账号身份：在手机上扫码/退出之后切回来，演示模式与账号数据
    // 的切换必须立刻反映到界面，不能等用户手动刷新。
    function onWake() {
      if (document.hidden) { return; }
      var wasDemo = !state.status || state.status.demo;
      loadStatus().then(function (status) {
        if (!status) { return; }
        if (wasDemo !== Boolean(status.demo)) {
          boot();                     // 登录或退出：整页数据都要换
          return;
        }
        if (state.current) { refreshOnce(); return; }
        if (!$("home-view").hidden) {
          loadDevices().then(renderHome).catch(function () { /* 忽略 */ });
        }
      }).catch(function () {
        if (state.current) { refreshOnce(); }
      });
    }
    window.addEventListener("focus", onWake);
    document.addEventListener("visibilitychange", onWake);

    // 键盘：Esc 关弹窗/回列表，/ 聚焦搜索，搜索框里 Enter 打开唯一匹配
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape") {
        var closed = closeTopLayer();
        if (!closed && !$("detail-view").hidden) { goHome(); }
        if (!closed && $("detail-view").hidden && state.filterRoom) {
          state.filterRoom = "";
          renderHome();
        }
        return;
      }
      var typing = event.target && (/^(INPUT|TEXTAREA|SELECT)$/
        .test(event.target.tagName) || event.target.isContentEditable);
      if (event.key === "/" && !typing) {
        event.preventDefault();
        if ($("search-input")) { $("search-input").focus(); }
        return;
      }
      if (event.key === "Enter" && event.target === $("search-input")) {
        var matches = visibleDevices();
        if (matches.length === 1) { openDevice(matches[0].did); }
      }
    });
  }

  // 关掉最上层的弹窗；没有可关的就返回 false（交给调用方决定下一步）
  function closeTopLayer() {
    var modals = ["login-modal", "timer-modal", "status-modal",
                  "account-modal"];
    for (var i = 0; i < modals.length; i += 1) {
      var modal = $(modals[i]);
      if (modal && !modal.hidden) { modal.hidden = true; return true; }
    }
    return false;
  }

  document.addEventListener("DOMContentLoaded", function () {
    syncToggle();
    try {
      state.hideReadonly = window.localStorage.getItem("mihome-hide-readonly") === "1";
    } catch (e) { /* 隐私模式 */ }
    bind();
    boot();
  });
})();
