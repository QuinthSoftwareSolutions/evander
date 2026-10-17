import { useEffect, useMemo, useRef, useState } from "react";
import { adminHash } from "../../adminRouting";
import { useAdminUi } from "../admin/AdminContext";
import { fetchJson } from "../admin/api";
import { useDocumentVisible } from "../admin/visibility";
import { browserCostDate, costAccountId, costAccountName, costCategoryName, costColors, costMoney, inferenceTokens, shiftCostDate,
  type InferenceCostReport, type InferenceTokenReport } from "./inferenceCosts";
import "./inferenceCosts.css";

const H = 300, LEFT = 82, RIGHT = 24, TOP = 22, BOTTOM = 55;
const plotH = H - TOP - BOTTOM;

function Chart({ report, kind, selected, tokenReport, colors }: { report: InferenceCostReport;
  kind: "daily" | "total" | "compare"; selected: string[]; tokenReport?: InferenceTokenReport;
  colors?: Map<string | null, string> }) {
  const [detail, setDetail] = useState("");
  const figure = useRef<HTMLElement>(null);
  const [width, setWidth] = useState(900);
  useEffect(() => {
    const element = figure.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.max(320, entry.contentRect.width)));
    observer.observe(element);
    return () => observer.disconnect();
  }, [kind, selected, report, tokenReport]);
  const tokens = kind === "daily" && !!tokenReport;
  const daily = tokens ? tokenReport.daily.map((day) => ({ date: day.date, cost: day.total_tokens,
    categories: day.categories.map((item) => ({ category: item.category, cost: item.total_tokens })) })) : report.daily;
  const exactValue = tokens ? inferenceTokens : costMoney;
  const axisValue = tokens ? (value: number) => new Intl.NumberFormat(undefined, {
    notation: "compact", maximumFractionDigits: 1,
  }).format(value) : costMoney;
  const plotW = width - LEFT - RIGHT;
  const barWidth = Math.min(48, plotW / Math.max(1, daily.length) * 0.8);
  const categories = [...new Set(tokens ? daily.flatMap((day) => day.categories.map((item) => item.category))
    : report.groups.map((group) => group.category))];
  const categoryColors = colors ?? costColors(categories);
  const accountColors = costColors(report.accounts.map((account) => account.id));
  const selectedAccounts = report.accounts.filter((account) => selected.includes(account.id));
  const accounts = selectedAccounts.filter((account) => report.groups.some((group) => costAccountId(group) === account.id && group.priced_events > 0));
  const unavailableAccounts = selectedAccounts.filter((account) => !accounts.includes(account));
  const series = kind === "total" ? [{ id: "total", name: "Total", color: "#497bdf",
    values: report.daily.map((day) => day.cumulative_cost) }]
    : accounts.map((account) => ({ id: account.id, name: costAccountName(account), color: accountColors.get(account.id)!,
      values: report.daily.map((day) => day.accounts.find((item) => item.id === account.id)?.cumulative_cost ?? 0) }));
  const maximum = Math.max(tokens ? 1 : 0.01, ...(kind === "daily" ? daily.map((day) => day.cost) : series.flatMap((item) => item.values)));
  const y = (value: number) => TOP + plotH * (1 - value / maximum);
  const x = (index: number) => LEFT + plotW * (index + 1) / Math.max(1, daily.length);
  const title = kind === "daily" ? (tokens ? "Daily inference tokens by category" : "Daily inference cost by category") : "Accumulated inference cost";
  const labelStep = Math.max(1, Math.ceil(daily.length / Math.max(2, Math.floor(plotW / 65))));
  if (kind === "compare" && !accounts.length) return <p role="status">Costs are unavailable for the selected provider / API key groups.</p>;
  return <figure ref={figure} className="inference-chart">
    <figcaption>{title}</figcaption>
    <svg viewBox={`0 0 ${width} ${H}`} role="group" aria-label={`${title}, ${tokens ? "tokens" : "USD"} by date`}>
      {[0, 1, 2, 3, 4].map((tick) => <g key={tick}>
        <line className="cost-grid-line" x1={LEFT} x2={width - RIGHT} y1={y(maximum * tick / 4)} y2={y(maximum * tick / 4)} />
        <text x={LEFT - 10} y={y(maximum * tick / 4) + 4} textAnchor="end">{axisValue(maximum * tick / 4)}</text>
      </g>)}
      <text x={LEFT} y={13}>{tokens ? "Tokens" : "USD"}</text>
      {daily.map((day, index) => index % labelStep === 0 || index === daily.length - 1
        ? <text key={day.date} x={kind === "daily" ? x(index) - plotW / daily.length / 2 : x(index)} y={H - 27} textAnchor="middle">{day.date.slice(5)}</text> : null)}
      <text x={width / 2} y={H - 5} textAnchor="middle">Date ({report.timezone})</text>
      {kind === "daily" ? daily.map((day, index) => {
        let offset = 0;
        return categories.map((category) => {
          const amount = day.categories.find((item) => item.category === category)?.cost ?? 0;
          const base = offset; offset += amount;
          if (!amount) return null;
          const label = `${day.date} · ${costCategoryName(category)}: ${exactValue(amount)}`;
          return <rect className="cost-bar" key={`${day.date}:${JSON.stringify(category)}`} tabIndex={0} role="img" aria-label={label}
            x={x(index) - plotW / daily.length / 2 - barWidth / 2} y={y(offset)}
            width={barWidth} height={Math.max(0, y(base) - y(offset))}
            fill={categoryColors.get(category)} onFocus={() => setDetail(label)} onMouseEnter={() => setDetail(label)}>
            <title>{label}</title>
          </rect>;
        });
      }) : series.map((item) => <g key={item.id}>
        <polyline fill="none" stroke={item.color} strokeWidth={2.5}
          points={`${LEFT},${y(0)} ${item.values.map((value, index) => `${x(index)},${y(value)}`).join(" ")}`} />
        {item.values.map((value, index) => {
          const label = `${report.daily[index].date} · ${item.name}: ${costMoney(value)} accumulated`;
          return <circle key={index} tabIndex={0} role="img" aria-label={label} cx={x(index)} cy={y(value)} r={3.5} fill={item.color}
            onFocus={() => setDetail(label)} onMouseEnter={() => setDetail(label)}><title>{label}</title></circle>;
        })}
      </g>)}
    </svg>
    <div className="cost-chart-detail" aria-live="polite">{detail || "Point to or focus a chart value for details."}</div>
    <ul className="cost-legend" aria-label="Chart legend">
      {(kind === "daily" ? categories.map((category) => ({ id: JSON.stringify(category), name: costCategoryName(category), color: categoryColors.get(category) })) : series)
        .map((item) => <li key={item.id}><span style={{ background: item.color }} />{item.name}</li>)}
    </ul>
    {kind === "compare" && unavailableAccounts.length > 0 && <p className="muted-text">Unavailable: {unavailableAccounts.map(costAccountName).join(", ")}</p>}
  </figure>;
}

function CostTable({ report }: { report: InferenceCostReport }) {
  return <div className="cost-table-scroll"><table className="data-table inference-cost-table">
    <caption>API inference cost by provider, API key, and category</caption>
    <thead><tr><th>Provider</th><th>API key</th><th>Category</th><th>Cost (USD)</th></tr></thead>
    <tbody>{report.accounts.map((account) => {
      const groups = report.groups.filter((group) => costAccountId(group) === account.id);
      const subtotal = groups.reduce((sum, group) => sum + (group.cost ?? 0), 0);
      const priced = groups.some((group) => group.priced_events > 0);
      return <CostTableGroup key={account.id} groups={groups} subtotal={subtotal} priced={priced} />;
    })}</tbody>
    <tfoot><tr><th colSpan={3}>Total{report.coverage.partial ? " (partial)" : ""}</th>
      <td>{report.coverage.priced_events || !report.coverage.partial ? costMoney(report.total_cost) : "Unavailable"}</td></tr></tfoot>
  </table></div>;
}

function CostTableGroup({ groups, subtotal, priced }: { groups: InferenceCostReport["groups"]; subtotal: number; priced: boolean }) {
  return <>{groups.map((group) => <tr key={JSON.stringify(group.category)}>
    <td>{group.provider}</td><td>{group.api_key_name ?? "Unknown key"}</td><td>{costCategoryName(group.category)}</td>
    <td>{group.cost === null ? "Unavailable" : costMoney(group.cost)}<MissingCosts count={group.unpriced_events} /></td>
  </tr>)}<tr className="cost-subtotal"><th colSpan={3}>{groups[0]?.provider} / {groups[0]?.api_key_name ?? "Unknown key"} subtotal</th>
    <td>{priced ? costMoney(subtotal) : "Unavailable"}<MissingCosts count={groups.reduce((sum, group) => sum + group.unpriced_events, 0)} /></td></tr></>;
}

function MissingCosts({ count }: { count: number }) {
  return count > 0 ? <small className="cost-coverage-note"
    title="These requests lack usage or pricing data, so their costs are excluded from this amount.">
    {count.toLocaleString()} {count === 1 ? "request" : "requests"} excluded
  </small> : null;
}

export function InferenceCostsPanel({ compact = false, refreshKey = 0 }: { compact?: boolean; refreshKey?: number }) {
  const { selectedDirectoryPath, setSelectedDirectoryPath } = useAdminUi();
  const visible = useDocumentVisible();
  const initial = useMemo(() => new URLSearchParams(compact ? "" : window.location.hash.split("?")[1] ?? ""), [compact]);
  const [period, setPeriod] = useState(initial.has("start") ? "custom" : "mtd");
  const [start, setStart] = useState(initial.get("start") || `${browserCostDate().slice(0, 7)}-01`);
  const [through, setThrough] = useState(initial.get("end") ? shiftCostDate(initial.get("end")!, -1) : browserCostDate());
  const [mode, setMode] = useState(initial.get("mode") === "compare" ? "compare" : "total");
  const [categoryMetric, setCategoryMetric] = useState(initial.get("category_metric") === "tokens" ? "tokens" : "cost");
  const [selected, setSelected] = useState<string[]>(() => {
    try { const value = JSON.parse(initial.get("groups") || "[]"); return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : []; }
    catch { return []; }
  });
  const timezone = initial.get("timezone") || Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  const [report, setReport] = useState<InferenceCostReport | null>(null);
  const [tokenReport, setTokenReport] = useState<InferenceTokenReport | null>(null);
  const [tokenLoading, setTokenLoading] = useState(false);
  const [tokenError, setTokenError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);
  const invalidPeriod = period === "custom" && (!start || !through || start > through);
  useEffect(() => {
    const directory = initial.get("directory_path");
    if (directory) setSelectedDirectoryPath(directory);
  }, [initial, setSelectedDirectoryPath]);
  useEffect(() => {
    if (!visible) return;
    const timer = window.setInterval(() => setRefresh((value) => value + 1), 60_000);
    return () => window.clearInterval(timer);
  }, [visible]);
  useEffect(() => {
    if (!visible || invalidPeriod) return;
    let active = true;
    const controller = new AbortController();
    const query = new URLSearchParams({ timezone, directory_path: selectedDirectoryPath });
    if (period === "custom") { query.set("start", start); query.set("end", shiftCostDate(through, 1)); }
    setLoading(true); setError(null); setReport(null);
    fetchJson<InferenceCostReport>(`/api/admin/reports/inference-costs?${query}`, { signal: controller.signal })
      .then((payload) => { if (active) { setReport(payload); setLoading(false); } })
      .catch((reason: unknown) => { if (active) { setError(reason instanceof Error ? reason.message : "Unable to load inference costs"); setLoading(false); } });
    return () => { active = false; controller.abort(); };
  }, [visible, invalidPeriod, period, start, through, timezone, selectedDirectoryPath, refresh, refreshKey]);
  useEffect(() => {
    if (compact || categoryMetric !== "tokens" || !visible || invalidPeriod) return;
    let active = true;
    const controller = new AbortController();
    const query = new URLSearchParams({ timezone, directory_path: selectedDirectoryPath });
    if (period === "custom") { query.set("start", start); query.set("end", shiftCostDate(through, 1)); }
    setTokenLoading(true); setTokenError(null); setTokenReport(null);
    fetchJson<InferenceTokenReport>(`/api/admin/reports/inference-tokens?${query}`, { signal: controller.signal })
      .then((payload) => { if (active) { setTokenReport(payload); setTokenLoading(false); } })
      .catch((reason: unknown) => { if (active) { setTokenError(reason instanceof Error ? reason.message : "Unable to load token usage"); setTokenLoading(false); } });
    return () => { active = false; controller.abort(); };
  }, [compact, categoryMetric, visible, invalidPeriod, period, start, through, timezone, selectedDirectoryPath, refresh, refreshKey]);
  const categoryColors = costColors(tokenReport?.daily.flatMap((day) => day.categories.map((item) => item.category)) ?? [],
    costColors(report?.groups.map((group) => group.category) ?? []));
  const reportLink = adminHash("reports", { view: "inference-costs", timezone, directory_path: selectedDirectoryPath,
    start: period === "custom" ? start : null, end: period === "custom" ? shiftCostDate(through, 1) : null,
    mode, groups: mode === "compare" ? JSON.stringify(selected) : null,
    category_metric: categoryMetric === "tokens" ? "tokens" : null });
  return <section className={`inference-cost-panel ${compact ? "inference-cost-compact" : ""}`} aria-label="Inference costs">
    <div className="cost-toolbar"><div><h2>Inference costs</h2><p className="muted-text">API costs · {period === "mtd" ? "Month to date" : "Custom period"} · {timezone}</p></div>
      {compact ? <a href={reportLink}>Full cost report</a> : <button type="button" onClick={() => setRefresh((value) => value + 1)}>Refresh costs</button>}
    </div>
    {!compact && <div className="cost-controls">
      <label>Period<select aria-label="Period" value={period} onChange={(event) => setPeriod(event.target.value)}><option value="mtd">Month to date</option><option value="custom">Custom period</option></select></label>
      {period === "custom" && <><label>From<input type="date" value={start} onChange={(event) => setStart(event.target.value)} /></label>
        <label>Through<input type="date" value={through} onChange={(event) => setThrough(event.target.value)} /></label></>}
    </div>}
    {invalidPeriod ? <p role="alert">Choose a valid start and end date.</p> : error ? <p role="alert">{error}</p> : loading ? <p role="status">Loading inference costs…</p> : report && <>
      <p className="cost-headline">{report.coverage.priced_events || !report.coverage.partial ? costMoney(report.total_cost) : "Unavailable"}
        {report.coverage.partial && <span>Partial total</span>}</p>
      <p className="muted-text">Provider-reported charges where available; otherwise estimates from configured prices.</p>
      {report.coverage.partial && <p role="status">
        {report.coverage.priced_events.toLocaleString()} of {(report.coverage.priced_events + report.coverage.unpriced_events).toLocaleString()} API requests have cost data.
        {report.coverage.unpriced_events > 0 && <> {report.coverage.unpriced_events.toLocaleString()} lack usage or pricing data and are excluded.</>}
        {report.coverage.unknown_billing_events > 0 && <> {report.coverage.unknown_billing_events.toLocaleString()} requests with unknown billing are also excluded.</>}
      </p>}
      {!report.groups.length && <p>No API inference recorded for this period.</p>}
      <div className="cost-controls">
        <label>Accumulated chart<select aria-label="Accumulated chart" value={mode} onChange={(event) => {
          setMode(event.target.value); if (event.target.value === "compare" && !selected.length) setSelected(report.accounts.map((account) => account.id));
        }}><option value="total">Total</option><option value="compare">Compare provider / API key</option></select></label>
        {mode === "compare" && <label>Provider / API key<select aria-label="Provider / API key" multiple size={Math.min(6, Math.max(2, report.accounts.length))} value={selected}
          onChange={(event) => setSelected([...event.target.selectedOptions].map((option) => option.value))}>
          {report.accounts.map((account) => <option key={account.id} value={account.id}>{costAccountName(account)}</option>)}
        </select></label>}
      </div>
      {!report.coverage.priced_events && report.coverage.partial
        ? <p role="status">No priced usage is available to chart for this period.</p>
        : mode === "compare" && !selected.some((id) => report.accounts.some((account) => account.id === id))
        ? <p>Select one or more provider / API key groups to compare.</p>
        : <Chart report={report} kind={mode === "compare" ? "compare" : "total"} selected={selected} />}
      {!compact && <>
        <div className="cost-controls"><label>Category chart<select aria-label="Category chart" value={categoryMetric}
          onChange={(event) => {
            setCategoryMetric(event.target.value);
            const params = new URLSearchParams(reportLink.split("?")[1] ?? "");
            if (event.target.value === "tokens") params.set("category_metric", "tokens"); else params.delete("category_metric");
            window.history.replaceState(null, "", `${reportLink.split("?")[0]}?${params}`);
          }}><option value="cost">Cost</option><option value="tokens">Tokens</option></select></label></div>
        {categoryMetric === "tokens" ? <>
          <p className="muted-text">Total tokens from recorded API and subscription inference. Earlier subscription history may be incomplete.</p>
          {tokenError ? <p role="alert">{tokenError}</p> : tokenLoading ? <p role="status">Loading token usage…</p> : tokenReport && <>
            {tokenReport.coverage.partial && <p role="status">Partial token usage: {tokenReport.coverage.unavailable_events.toLocaleString()} {tokenReport.coverage.unavailable_events === 1 ? "usage record lacks token counts and is" : "usage records lack token counts and are"} excluded.</p>}
            {tokenReport.coverage.available_events > 0 ? <Chart key="tokens" report={report} tokenReport={tokenReport} kind="daily" selected={[]} colors={categoryColors} />
              : <p role="status">No token counts are available for this period.</p>}
          </>}
        </> : (report.coverage.priced_events > 0 || !report.coverage.partial) && <Chart key="cost" report={report} kind="daily" selected={[]} colors={categoryColors} />}
        <CostTable report={report} />
      </>}
    </>}
  </section>;
}
