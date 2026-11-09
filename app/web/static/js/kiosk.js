// 사이드바에서 정류장을 고르면 오른쪽에 키오스크 화면을 그린다.
// 서버가 준 문자열은 textContent 로만 넣는다.
(() => {
  const app = document.getElementById("app");
  const base = app.dataset.apiBase;
  const refreshMs = Number(app.dataset.refresh) * 1000;
  const SOON_SECONDS = 180;
  const ARRIVING_SECONDS = 60;
  const CROWD = { 1: "여유", 2: "보통", 3: "혼잡", 4: "매우 혼잡" };
  const DIRECTION = { commute: "등교", return: "하교", both: "등하교" };

  let station = null;
  let routes = {};
  let data = null;
  let fetchedMs = 0;
  let lines = null;
  let filter = null;
  let loadToken = 0;

  const $ = (id) => document.getElementById(id);
  const el = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  };

  const seconds = (bus) => {
    if (!bus) return null;
    if (bus.predictSec !== null) return bus.predictSec;
    return bus.predictMinutes !== null ? bus.predictMinutes * 60 : null;
  };

  const remaining = (bus) => {
    const sec = seconds(bus);
    return sec === null ? null : Math.max(0, sec - (Date.now() - fetchedMs) / 1000);
  };

  const timeLabel = (sec) => {
    if (sec === null) return "-";
    return sec < ARRIVING_SECONDS ? "곧 도착" : `${Math.ceil(sec / 60)}분`;
  };

  const destination = (a) => {
    const via = routes[a.route] && routes[a.route].via;
    return via ? `${via} 경유 ${a.destName}행` : `${a.destName}행`;
  };

  function crowdBadge(bus) {
    const label = bus && CROWD[bus.crowded];
    if (!label) return null;
    return el("span", `badge c${bus.crowded}`, label);
  }

  function details(bus, withBadge = true) {
    const box = el("span", "details");
    if (!bus) return box;
    const badge = withBadge && crowdBadge(bus);
    if (badge) box.append(badge);
    const parts = [];
    if (bus.locationNo !== null) parts.push(`${bus.locationNo}정거장 전`);
    if (bus.remainSeatCnt !== null) parts.push(`빈자리 ${bus.remainSeatCnt}`);
    if (bus.lowPlate === 1) parts.push("저상");
    if (parts.length) box.append(el("span", "text", parts.join(" / ")));
    return box;
  }

  const notice = (text) => {
    $("notice").hidden = !text;
    $("notice").textContent = text || "";
  };

  function renderFilters() {
    const box = $("filters");
    box.replaceChildren();
    if (!data) return;
    const names = [...new Set(data.arrivals.map((a) => a.route))];
    const chip = (name, label) => {
      const b = el("button", name === filter ? "chip on" : "chip", label);
      b.type = "button";
      b.addEventListener("click", () => {
        filter = name;
        render();
      });
      return b;
    };
    box.append(chip(null, "전체"));
    for (const name of names) box.append(chip(name, name));
  }

  function renderSoon(items) {
    const list = $("soon");
    list.replaceChildren();
    const soon = items.filter((x) => x.sec !== null && x.sec <= SOON_SECONDS);
    $("soon-empty").hidden = soon.length > 0;
    for (const x of soon) {
      const li = el("li", x.sec < ARRIVING_SECONDS ? "soon-item now" : "soon-item");
      li.append(el("span", "route", x.a.route));
      const mid = el("span", "mid");
      mid.append(el("span", "dest", destination(x.a)), details(x.a.buses[0]));
      li.append(mid, el("span", "time", timeLabel(x.sec)));
      list.append(li);
    }
  }

  function renderRows(items) {
    const body = $("rows");
    body.replaceChildren();
    for (const x of items) {
      const tr = el("tr", x.sec === null ? "idle" : "");
      const route = el("td", "route", x.a.route);
      const dir = routes[x.a.route] && DIRECTION[routes[x.a.route].direction];
      if (dir) route.append(el("small", "dir", dir));
      tr.append(route, el("td", "dest", destination(x.a)));
      const first = el("td", "time", x.sec === null ? "운행 정보 없음" : timeLabel(x.sec));
      const badge1 = crowdBadge(x.a.buses[0]);
      if (badge1) first.append(badge1);
      tr.append(first);
      const second = el("td", "time", timeLabel(remaining(x.a.buses[1])));
      const badge2 = crowdBadge(x.a.buses[1]);
      if (badge2) second.append(badge2);
      tr.append(second);
      const info = el("td", "info");
      info.append(details(x.a.buses[0], false));
      tr.append(info);
      body.append(tr);
      if (x.a.state === "running") {
        const row = el("tr", "line-row");
        const cell = el("td");
        cell.colSpan = 5;
        cell.append(renderTrack(x.a));
        row.append(cell);
        body.append(row);
      }
    }
  }

  // 버스 위치는 정류장 순서(staOrder) 에서 몇 정거장 전(locationNo) 인지로 구한다.
  const MIN_SPAN = 5;
  const MAX_SPAN = 9;

  function lineFor(a) {
    const entry = (lines || []).find((l) => l.route === a.route && l.staOrder === a.staOrder);
    if (!entry) return null;
    const buses = a.buses.filter((b) => b.locationNo !== null && b.locationNo < a.staOrder);
    if (!buses.length) return null;
    const far = Math.max(...buses.map((b) => b.locationNo));
    const start = Math.max(1, a.staOrder - Math.min(MAX_SPAN, Math.max(MIN_SPAN, far)));
    const cols = entry.stations.filter((s) => s.seq >= start && s.seq <= a.staOrder);
    return { cols, start, buses };
  }

  const SVG = "http://www.w3.org/2000/svg";

  function busIcon() {
    const svg = document.createElementNS(SVG, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("class", "bus-icon");
    svg.setAttribute("aria-hidden", "true");
    const body = document.createElementNS(SVG, "path");
    body.setAttribute(
      "d",
      "M5 3h14a2 2 0 0 1 2 2v12a2 2 0 0 1-1 1.7V20a1 1 0 0 1-1 1h-1.5a1 1 0 0 1-1-1v-1h-9v1a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1v-1.3A2 2 0 0 1 3 17V5a2 2 0 0 1 2-2zm0 3v5h14V6zm2.5 9a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3zm9 0a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3z"
    );
    body.setAttribute("fill-rule", "evenodd");
    svg.append(body);
    return svg;
  }

  // 노선도 한 줄. 버스는 정류소 점 위에 아이콘과 남은 시간으로 찍는다.
  function renderTrack(a) {
    const info = lineFor(a);
    if (!info) return el("p", "empty", "노선도를 불러오는 중이다.");
    const track = el("div", "track");
    for (const s of info.cols) {
      const col = el("div", s.seq === a.staOrder ? "stn here" : "stn");
      const here = info.buses.filter((b) => Math.max(info.start, a.staOrder - b.locationNo) === s.seq);
      for (const bus of here) {
        const far = a.staOrder - bus.locationNo < info.start;
        const label = far ? `${bus.locationNo}정거장 전` : timeLabel(remaining(bus));
        const mark = el("span", `bus c${bus.crowded || 0}${far ? " far" : ""}`);
        mark.append(busIcon(), el("span", "bus-time", label));
        col.append(mark);
      }
      col.append(el("i", "dot"), el("span", "name", s.name));
      track.append(col);
    }
    return track;
  }

  // 정류소 순서는 거의 안 바뀌어서 정류장을 고른 뒤 한 번만 받는다.
  async function loadLines(token) {
    if (lines !== null) return;
    lines = [];
    try {
      const response = await fetch(`${base}/stops/${station}/stations`);
      if (token !== loadToken) return;
      if (!response.ok) return (lines = null);
      lines = (await response.json()).lines;
      render();
    } catch (error) {
      if (token === loadToken) lines = null;
    }
  }

  function render() {
    $("clock").textContent = new Date().toLocaleTimeString("ko-KR", { hour12: false });
    if (!station || !data) return;
    renderFilters();
    const items = data.arrivals
      .filter((a) => filter === null || a.route === filter)
      .map((a) => ({ a, sec: remaining(a.buses[0]) }));
    items.sort((p, q) => (p.sec ?? Infinity) - (q.sec ?? Infinity));
    renderSoon(items);
    renderRows(items);
    $("updated").textContent = `${Math.round((Date.now() - fetchedMs) / 1000)}초 전 정보`;
  }

  const number = (value) => (value === null ? "-" : value.toLocaleString("ko-KR"));

  // 남은 quota 는 GBIS 응답 헤더의 마지막 값이다. 이 조회는 GBIS 를 부르지 않는다.
  async function loadQuota() {
    if (document.hidden) return;
    const box = $("quota");
    try {
      const response = await fetch(`${base}/status`);
      if (!response.ok) return;
      const status = await response.json();
      box.replaceChildren();
      if (status.keysConfigured === 0) {
        box.append(el("dt", "warn", "인증키가 없다"));
        return;
      }
      for (const [service, label] of [["list", "목록조회"], ["item", "항목조회"]]) {
        const keys = status.quota[service];
        const text = keys
          .map((k) => (keys.length > 1 ? `키${k.keyNo} ${number(k.remaining)}` : number(k.remaining)))
          .join(", ");
        box.append(el("dt", "", label), el("dd", "", text));
      }
      if (status.quotaGuard) box.append(el("dt", "warn", "쓸 수 있는 키가 없다"));
    } catch (error) {
      /* 사이드바 표시일 뿐이라 실패해도 화면을 막지 않는다 */
    }
  }

  async function loadRoutes(token) {
    const response = await fetch(`${base}/stops/${station}`);
    if (!response.ok || token !== loadToken) return;
    routes = {};
    for (const r of (await response.json()).routeDetails) routes[r.name] = r;
  }

  async function refresh(token = loadToken) {
    if (!station || document.hidden) return;
    try {
      const response = await fetch(`${base}/stops/${station}/arrivals`);
      if (token !== loadToken) return;
      if (response.status === 429) return notice("요청이 많아 잠시 쉰다.");
      if (!response.ok) return notice("도착 정보를 가져오지 못했다.");
      data = await response.json();
      fetchedMs = Date.parse(data.fetchedAt);
      notice(data.stale ? "최신 정보를 받지 못해 이전 정보를 보여 준다." : "");
      render();
      loadLines(token);
      loadQuota();
    } catch (error) {
      if (token === loadToken) notice("서버에 연결하지 못했다.");
    }
  }

  function select(number, name, push) {
    station = number;
    loadToken += 1;
    data = null;
    routes = {};
    lines = null;
    filter = null;
    $("placeholder").hidden = true;
    $("kiosk").hidden = false;
    $("stop-name").textContent = name;
    $("stop-no").textContent = number;
    $("soon").replaceChildren();
    $("rows").replaceChildren();
    $("filters").replaceChildren();
    $("soon-empty").hidden = true;
    $("updated").textContent = "";
    notice("불러오는 중이다.");
    for (const item of document.querySelectorAll(".side-item")) {
      item.classList.toggle("active", item.dataset.station === number);
    }
    document.title = `${name} - 산돌이 버스`;
    if (push) history.pushState({ station: number, name }, "", `/demo/stops/${number}`);
    const token = loadToken;
    loadRoutes(token).then(() => refresh(token));
  }

  for (const item of document.querySelectorAll(".side-item")) {
    item.addEventListener("click", (event) => {
      event.preventDefault();
      select(item.dataset.station, item.dataset.name, true);
    });
  }

  window.addEventListener("popstate", () => {
    const match = location.pathname.match(/\/demo\/stops\/(\d{5})$/);
    const item = match && document.querySelector(`.side-item[data-station="${match[1]}"]`);
    if (item) return select(item.dataset.station, item.dataset.name, false);
    station = null;
    loadToken += 1;
    $("kiosk").hidden = true;
    $("placeholder").hidden = false;
    for (const i of document.querySelectorAll(".side-item")) i.classList.remove("active");
  });

  // 탭이 숨겨진 동안은 GBIS 호출이 늘지 않게 갱신하지 않는다.
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) {
      refresh();
      loadQuota();
    }
  });

  setInterval(() => refresh(), refreshMs);
  loadQuota();
  setInterval(render, 1000);
  const initial = app.dataset.station;
  if (initial) {
    const item = document.querySelector(`.side-item[data-station="${initial}"]`);
    select(initial, item ? item.dataset.name : "", false);
  } else {
    render();
  }
})();
