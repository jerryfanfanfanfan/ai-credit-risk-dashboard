const state = {
  data: null,
  category: "全部",
  query: "",
  chartModels: new Map(),
};

const statusLabel = {
  green: "正常",
  yellow: "关注",
  red: "压力",
};

const escapeHtml = value => String(value ?? "")
  .replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;")
  .replaceAll("'", "&#039;");

const formatValue = (value, unit) => {
  if (value === undefined || value === null || Number.isNaN(value)) return "--";
  const abs = Math.abs(value);
  const digits = abs >= 100 ? 0 : abs >= 10 ? 1 : 2;
  return `${Number(value).toFixed(digits)}${unit === "%" ? "%" : ""}`;
};

const formatAxisValue = value => {
  const abs = Math.abs(value);
  if (abs >= 1000) return `${(value / 1000).toFixed(abs >= 10000 ? 0 : 1)}k`;
  if (abs >= 100) return value.toFixed(0);
  if (abs >= 10) return value.toFixed(1);
  return value.toFixed(2);
};

const formatDateTick = (dateValue, longRange) => {
  const date = String(dateValue);
  return longRange ? date.slice(0, 7) : date.slice(5);
};

async function loadData() {
  const response = await fetch("./data/metrics.json", { cache: "no-store" });
  if (!response.ok) throw new Error(`Cannot load metrics.json: ${response.status}`);
  state.data = await response.json();
  render();
}

function render() {
  renderSummary();
  renderFilters();
  renderCards();
  renderSources();
  renderWarnings();
}

function renderSummary() {
  const value = Number(state.data.stress_index || 0);
  const status = state.data.stress_status || "green";
  document.getElementById("indexValue").textContent = value.toFixed(1);
  const chip = document.getElementById("indexStatus");
  chip.textContent = statusLabel[status] || status;
  chip.className = `status-chip ${status}`;
  document.getElementById("asOfText").textContent = `截至 ${state.data.as_of}，数据生成于 ${state.data.generated_at}`;
}

function renderFilters() {
  const categories = ["全部", ...new Set(state.data.metrics.map(metric => metric.category).filter(Boolean))];
  const wrap = document.getElementById("categoryFilters");
  wrap.innerHTML = categories.map(category => `
    <button type="button" class="${category === state.category ? "active" : ""}" data-category="${escapeHtml(category)}">${escapeHtml(category)}</button>
  `).join("");
  wrap.querySelectorAll("button").forEach(button => {
    button.addEventListener("click", () => {
      state.category = button.dataset.category;
      renderFilters();
      renderCards();
    });
  });
}

function chartGeometry(metric) {
  const rows = state.data.series[metric.id] || [];
  if (!rows.length) return null;

  const width = 560;
  const height = 210;
  const pad = { left: 54, right: 18, top: 18, bottom: 38 };
  const values = rows.map(row => Number(row.value));
  const times = rows.map(row => Date.parse(`${row.date}T00:00:00Z`));
  let minValue = Math.min(...values);
  let maxValue = Math.max(...values);
  const valueRange = maxValue - minValue;
  const valuePadding = valueRange ? valueRange * 0.12 : Math.max(Math.abs(maxValue) * 0.1, 1);
  minValue -= valuePadding;
  maxValue += valuePadding;

  const minTime = Math.min(...times);
  const maxTime = Math.max(...times);
  const x = time => minTime === maxTime
    ? pad.left + (width - pad.left - pad.right) / 2
    : pad.left + (time - minTime) * (width - pad.left - pad.right) / (maxTime - minTime);
  const y = value => pad.top + (maxValue - value) * (height - pad.top - pad.bottom) / (maxValue - minValue);
  const points = rows.map((row, index) => ({
    ...row,
    index,
    x: x(times[index]),
    y: y(values[index]),
  }));
  const yTicks = Array.from({ length: 4 }, (_, index) => minValue + index * (maxValue - minValue) / 3);
  const tickIndexes = [...new Set([0, Math.round((rows.length - 1) / 2), rows.length - 1])];
  const longRange = maxTime - minTime > 370 * 24 * 60 * 60 * 1000;

  return { width, height, pad, points, yTicks, tickIndexes, longRange };
}

function detailedChart(metric) {
  const model = chartGeometry(metric);
  if (!model) return '<div class="chart-empty">暂无历史序列</div>';
  state.chartModels.set(metric.id, model);
  const { width, height, pad, points, yTicks, tickIndexes, longRange } = model;
  const path = points.length > 1
    ? points.map((point, index) => `${index ? "L" : "M"} ${point.x.toFixed(2)} ${point.y.toFixed(2)}`).join(" ")
    : "";
  const unitLabel = metric.unit === "%" ? "%" : metric.unit;

  return `
    <div class="metric-chart" data-metric-id="${escapeHtml(metric.id)}">
      <svg class="detail-chart" viewBox="0 0 ${width} ${height}" role="img" tabindex="0" aria-label="${escapeHtml(metric.name)}历史走势">
        <text class="axis-unit" x="${pad.left}" y="11">${escapeHtml(unitLabel)}</text>
        ${yTicks.map(value => {
          const y = pad.top + (model.yTicks[model.yTicks.length - 1] - value) * (height - pad.top - pad.bottom) / (model.yTicks[model.yTicks.length - 1] - model.yTicks[0]);
          return `<line class="chart-gridline" x1="${pad.left}" x2="${width - pad.right}" y1="${y}" y2="${y}"></line><text class="axis-label y-label" x="${pad.left - 8}" y="${y + 4}">${formatAxisValue(value)}</text>`;
        }).join("")}
        <line class="chart-axis" x1="${pad.left}" x2="${pad.left}" y1="${pad.top}" y2="${height - pad.bottom}"></line>
        <line class="chart-axis" x1="${pad.left}" x2="${width - pad.right}" y1="${height - pad.bottom}" y2="${height - pad.bottom}"></line>
        ${tickIndexes.map(index => {
          const point = points[index];
          const anchor = index === 0 ? "start" : index === points.length - 1 ? "end" : "middle";
          return `<line class="chart-tick" x1="${point.x}" x2="${point.x}" y1="${height - pad.bottom}" y2="${height - pad.bottom + 5}"></line><text class="axis-label x-label" text-anchor="${anchor}" x="${point.x}" y="${height - 12}">${formatDateTick(point.date, longRange)}</text>`;
        }).join("")}
        ${path ? `<path class="chart-line" d="${path}"></path>` : ""}
        ${points.length === 1 ? `<circle class="single-point" cx="${points[0].x}" cy="${points[0].y}" r="4"></circle>` : ""}
        <g class="chart-hover is-hidden">
          <line class="hover-crosshair" x1="0" x2="0" y1="${pad.top}" y2="${height - pad.bottom}"></line>
          <circle class="hover-marker" cx="0" cy="0" r="5"></circle>
        </g>
        <rect class="chart-interaction" x="${pad.left}" y="${pad.top}" width="${width - pad.left - pad.right}" height="${height - pad.top - pad.bottom}"></rect>
      </svg>
      <div class="chart-tooltip" role="tooltip" aria-hidden="true"></div>
    </div>
  `;
}

function seriesStats(metric) {
  const rows = state.data.series[metric.id] || [];
  const latest = rows.at(-1);
  const previous = rows.at(-2);
  const values = rows.map(row => Number(row.value));
  const delta = latest && previous ? Number(latest.value) - Number(previous.value) : null;
  const isWorse = delta === null ? null : metric.thresholds.direction === "low_is_stress" ? delta < 0 : delta > 0;
  return {
    count: rows.length,
    delta,
    deltaClass: delta === null || delta === 0 ? "neutral" : isWorse ? "worse" : "better",
    min: values.length ? Math.min(...values) : null,
    max: values.length ? Math.max(...values) : null,
  };
}

function configFor(metric) {
  return state.data.config.metrics.find(item => item.id === metric.id) || metric;
}

function renderCards() {
  const query = state.query.trim().toLowerCase();
  const metrics = state.data.metrics
    .filter(metric => !metric.missing)
    .filter(metric => state.category === "全部" || metric.category === state.category)
    .filter(metric => {
      const config = configFor(metric);
      const haystack = `${metric.name} ${metric.category} ${metric.public_proxy} ${metric.primary_source} ${config.meaning || ""} ${config.implication || ""}`;
      return !query || haystack.toLowerCase().includes(query);
    });
  const grid = document.getElementById("metricGrid");
  state.chartModels.clear();
  grid.innerHTML = metrics.map(metric => {
    const config = configFor(metric);
    const stats = seriesStats(metric);
    const deltaText = stats.delta === null
      ? "暂无前值"
      : `${stats.delta > 0 ? "+" : ""}${formatValue(stats.delta, metric.unit)}${metric.unit !== "%" ? ` ${escapeHtml(metric.unit)}` : ""}`;
    const rangeText = stats.min === null
      ? "--"
      : `${formatValue(stats.min, metric.unit)} - ${formatValue(stats.max, metric.unit)}${metric.unit !== "%" ? ` ${escapeHtml(metric.unit)}` : ""}`;
    const meaning = config.meaning || config.notes || metric.notes;
    const implication = config.implication || "指标进入黄色或红色区间时，说明相关信用风险高于正常水平。";

    return `
      <article class="metric-card ${escapeHtml(metric.status)}">
        <div class="metric-head">
          <div class="metric-title" tabindex="0" aria-label="${escapeHtml(metric.name)}：${escapeHtml(meaning)} ${escapeHtml(implication)}">
            <span class="metric-name">${escapeHtml(metric.name)}</span>
            <span class="info-mark" aria-hidden="true">i</span>
            <div class="metric-explainer" role="tooltip">
              <strong>指标含义</strong>
              <p>${escapeHtml(meaning)}</p>
              <strong>风险暗示</strong>
              <p>${escapeHtml(implication)}</p>
            </div>
          </div>
          <span class="metric-status">${statusLabel[metric.status]}</span>
        </div>
        <div class="metric-value-row">
          <div class="metric-value">
            <strong>${formatValue(metric.value, metric.unit)}</strong>
            <span>${metric.unit !== "%" ? escapeHtml(metric.unit) : ""}</span>
          </div>
          <span class="metric-date">${escapeHtml(metric.date)}</span>
        </div>
        <div class="stat-grid">
          <div><span>较前值</span><strong class="${stats.deltaClass}">${deltaText}</strong></div>
          <div><span>压力评分</span><strong>${formatValue(metric.score, "")} / 100</strong></div>
          <div><span>历史压力分位</span><strong>${formatValue(metric.percentile, "%")}</strong></div>
          <div><span>样本 / 历史区间</span><strong>${stats.count} / ${rangeText}</strong></div>
        </div>
        ${detailedChart(metric)}
        <div class="metric-meta">
          <div><b>红黄绿阈值</b> 绿 ${escapeHtml(metric.thresholds.green)} · 黄 ${escapeHtml(metric.thresholds.yellow)} · 红 ${escapeHtml(metric.thresholds.red)} · ${metric.thresholds.direction === "low_is_stress" ? "数值越低压力越大" : "数值越高压力越大"}</div>
          <div><b>来源</b> ${escapeHtml(metric.source)} · ${escapeHtml(metric.quality)} · ${escapeHtml(metric.frequency)}</div>
          <div><b>数据备注</b> ${escapeHtml(metric.notes)}</div>
        </div>
      </article>
    `;
  }).join("");
  bindChartInteractions();
}

function nearestPoint(points, x) {
  let low = 0;
  let high = points.length - 1;
  while (low < high) {
    const mid = Math.floor((low + high) / 2);
    if (points[mid].x < x) low = mid + 1;
    else high = mid;
  }
  const current = points[low];
  const previous = points[Math.max(0, low - 1)];
  return Math.abs(previous.x - x) <= Math.abs(current.x - x) ? previous : current;
}

function showChartPoint(svg, metric, point) {
  const wrapper = svg.closest(".metric-chart");
  const tooltip = wrapper.querySelector(".chart-tooltip");
  const hover = svg.querySelector(".chart-hover");
  const line = hover.querySelector(".hover-crosshair");
  const marker = hover.querySelector(".hover-marker");
  const model = state.chartModels.get(metric.id);
  line.setAttribute("x1", point.x);
  line.setAttribute("x2", point.x);
  marker.setAttribute("cx", point.x);
  marker.setAttribute("cy", point.y);
  hover.classList.remove("is-hidden");
  svg.dataset.activeIndex = point.index;

  tooltip.innerHTML = `
    <strong>${escapeHtml(point.date)}</strong>
    <span>${formatValue(point.value, metric.unit)}${metric.unit !== "%" ? ` ${escapeHtml(metric.unit)}` : ""}</span>
    <small>压力评分 ${formatValue(point.score, "")} / 100</small>
  `;
  tooltip.setAttribute("aria-hidden", "false");
  tooltip.classList.add("visible");
  const renderedWidth = svg.clientWidth;
  const renderedHeight = svg.clientHeight;
  const left = point.x / model.width * renderedWidth;
  const top = point.y / model.height * renderedHeight;
  tooltip.style.left = `${Math.max(98, Math.min(wrapper.clientWidth - 98, left))}px`;
  tooltip.style.top = `${top}px`;
  tooltip.classList.toggle("below", top < 72);
}

function hideChartPoint(svg) {
  const wrapper = svg.closest(".metric-chart");
  svg.querySelector(".chart-hover").classList.add("is-hidden");
  const tooltip = wrapper.querySelector(".chart-tooltip");
  tooltip.classList.remove("visible", "below");
  tooltip.setAttribute("aria-hidden", "true");
}

function bindChartInteractions() {
  document.querySelectorAll(".metric-chart").forEach(wrapper => {
    const metricId = wrapper.dataset.metricId;
    const metric = state.data.metrics.find(item => item.id === metricId);
    const model = state.chartModels.get(metricId);
    const svg = wrapper.querySelector("svg");
    if (!metric || !model || !svg) return;

    svg.addEventListener("pointermove", event => {
      const rect = svg.getBoundingClientRect();
      const svgX = (event.clientX - rect.left) * model.width / rect.width;
      showChartPoint(svg, metric, nearestPoint(model.points, svgX));
    });
    svg.addEventListener("pointerleave", () => {
      if (document.activeElement !== svg) hideChartPoint(svg);
    });
    svg.addEventListener("focus", () => {
      const index = Number(svg.dataset.activeIndex ?? model.points.length - 1);
      showChartPoint(svg, metric, model.points[index]);
    });
    svg.addEventListener("blur", () => hideChartPoint(svg));
    svg.addEventListener("keydown", event => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      let index = Number(svg.dataset.activeIndex ?? model.points.length - 1);
      if (event.key === "ArrowLeft") index = Math.max(0, index - 1);
      if (event.key === "ArrowRight") index = Math.min(model.points.length - 1, index + 1);
      if (event.key === "Home") index = 0;
      if (event.key === "End") index = model.points.length - 1;
      showChartPoint(svg, metric, model.points[index]);
    });
  });
}

function renderSources() {
  document.getElementById("sourceTable").innerHTML = state.data.config.metrics.map(metric => `
    <tr>
      <td>${escapeHtml(metric.name)}</td>
      <td>${escapeHtml(metric.frequency)}</td>
      <td>${escapeHtml(metric.primary_source)}</td>
      <td>${escapeHtml(metric.public_proxy)}</td>
    </tr>
  `).join("");
}

function renderWarnings() {
  const band = document.getElementById("warningBand");
  const list = document.getElementById("warningList");
  const warnings = state.data.warnings || [];
  band.hidden = !warnings.length;
  list.innerHTML = warnings.map(warning => `<li>${escapeHtml(warning)}</li>`).join("");
}

document.getElementById("refreshButton").addEventListener("click", loadData);
document.getElementById("searchInput").addEventListener("input", event => {
  state.query = event.target.value;
  renderCards();
});

loadData().catch(error => {
  document.body.innerHTML = `<main class="warning-band"><h1>无法读取数据</h1><p>${escapeHtml(error.message)}</p><p>请先运行更新脚本生成 dashboard/data/metrics.json。</p></main>`;
});
