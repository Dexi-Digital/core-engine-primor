import Link from "next/link";
import { redirect } from "next/navigation";

import { ApiError, apiFetch } from "@/lib/api";
import { PageHeader, StatCard, StatusBadge } from "@/components/ui/primitives";
import {
  IconArrowRight,
  IconBolt,
  IconSparkles,
  IconStethoscope,
  IconBuilding,
  IconUsers,
  IconTruck,
  IconGavel,
  IconShield,
  IconDoc,
  IconDollar,
  IconRefresh,
  IconCloud,
  IconActivity,
  IconBell,
} from "@/components/ui/icons";

type HomeData = {
  kpis: Record<string, number | null>;
  last_diagnostico_run: {
    id: number;
    started_at: string | null;
    finished_at: string | null;
    status: string;
    total_findings: number;
    ok_count: number;
    vencendo_count: number;
    vencido_count: number;
    ausente_count: number;
  } | null;
  findings_por_area: Record<
    string,
    { ok: number; vencendo: number; vencido: number; ausente: number }
  >;
  integrations: Array<{
    key: string;
    label: string;
    descr: string;
    status: "ok" | "idle" | "pending" | "error";
    last_event: string;
    configured: boolean;
  }>;
  feed: Array<{
    ts: string | null;
    kind: string;
    title: string;
    by: string;
  }>;
};

const INTEGRATION_ICON: Record<string, React.ReactNode> = {
  pncp: <IconGavel />,
  detran: <IconTruck />,
  crea: <IconDoc />,
  onedrive: <IconCloud />,
  documentai: <IconSparkles />,
  resend: <IconBell />,
  dominio: <IconDollar />,
  totvs: <IconDollar />,
};

const KIND_ICON: Record<string, React.ReactNode> = {
  diagnostico: <IconStethoscope width={14} height={14} />,
  onedrive: <IconCloud width={14} height={14} />,
  parte_diaria: <IconTruck width={14} height={14} />,
  detran: <IconTruck width={14} height={14} />,
};

const AREA_LABEL: Record<string, string> = {
  dp: "DP / Pessoas",
  sst: "Saúde & Segurança",
  frota: "Frota",
  empresa: "Empresa",
  obra: "Obras",
};

function relTime(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  const diff = (Date.now() - d.getTime()) / 1000;
  if (diff < 60) return "agora";
  if (diff < 3600) return `${Math.floor(diff / 60)}min atrás`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h atrás`;
  if (diff < 7 * 86400) return `${Math.floor(diff / 86400)}d atrás`;
  return d.toLocaleDateString("pt-BR");
}

async function load(): Promise<HomeData> {
  try {
    return await apiFetch<HomeData>("/api/v1/overview/home");
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) redirect("/logout");
    throw err;
  }
}

export default async function Home() {
  const data = await load();
  const k = data.kpis;
  const conf = k.conformidade_pct;
  const confTone: "success" | "warning" | "danger" =
    conf == null
      ? "warning"
      : conf >= 85
      ? "success"
      : conf >= 60
      ? "warning"
      : "danger";

  return (
    <div className="mx-auto max-w-[1400px]">
      <PageHeader
        eyebrow={
          <span className="inline-flex items-center gap-2">
            <span className="led led-ok led-pulse" /> Sistema operacional
          </span>
        }
        title="Comando Central"
        subtitle="Governança documental, integração com 8 fontes externas e automação do ciclo operacional da construtora."
        actions={
          <>
            <Link href="/diagnostico" className="btn btn-secondary">
              <IconStethoscope width={14} height={14} /> Abrir diagnóstico
            </Link>
            <Link href="/licitacoes" className="btn btn-primary">
              <IconBolt width={14} height={14} /> Monitorar licitações
            </Link>
          </>
        }
      />

      {/* --- KPIs hero --- */}
      <div className="grid gap-4 fade-in md:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label="Conformidade documental"
          value={conf == null ? "—" : `${conf.toFixed(1)}%`}
          unit={conf != null ? "OK" : ""}
          hint={
            data.last_diagnostico_run
              ? `Run #${data.last_diagnostico_run.id} · ${data.last_diagnostico_run.total_findings} findings`
              : "Rodar primeiro diagnóstico"
          }
          tone={confTone}
          icon={<IconStethoscope width={14} height={14} />}
        />
        <StatCard
          label="Documentos vencendo (30d)"
          value={
            (k.aso_vencendo ?? 0) + (k.cert_vencendo ?? 0)
          }
          hint={`${k.aso_vencendo ?? 0} ASOs · ${
            k.cert_vencendo ?? 0
          } certidões`}
          tone="warning"
          icon={<IconBell width={14} height={14} />}
        />
        <StatCard
          label="Críticos (vencidos)"
          value={(k.aso_vencido ?? 0) + (k.cert_vencida ?? 0)}
          hint={`${k.aso_vencido ?? 0} ASOs · ${k.cert_vencida ?? 0} certidões`}
          tone="danger"
          icon={<IconShield width={14} height={14} />}
        />
        <StatCard
          label="Atividade 7d"
          value={(k.partes_7d ?? 0) + (k.licitacoes_7d ?? 0)}
          hint={`${k.partes_7d ?? 0} partes · ${
            k.licitacoes_7d ?? 0
          } licitações`}
          tone="accent"
          icon={<IconActivity width={14} height={14} />}
        />
      </div>

      {/* --- Linha operacional --- */}
      <div className="mt-4 grid gap-4 fade-in fade-in-1 md:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label="Funcionários ativos"
          value={k.funcionarios_ativos ?? 0}
          unit={`/ ${k.funcionarios_total ?? 0}`}
          hint={`${k.afastados ?? 0} em afastamento`}
          icon={<IconUsers width={14} height={14} />}
        />
        <StatCard
          label="Veículos em obra"
          value={k.veiculos_ativos ?? 0}
          unit={`/ ${k.veiculos_total ?? 0}`}
          hint={`${k.consultas_detran_30d ?? 0} consultas Detran (30d)`}
          icon={<IconTruck width={14} height={14} />}
        />
        <StatCard
          label="Partes diárias (OCR)"
          value={k.partes_total ?? 0}
          hint={`+${k.partes_7d ?? 0} nos últimos 7d`}
          icon={<IconSparkles width={14} height={14} />}
        />
        <StatCard
          label="Licitações monitoradas"
          value={k.licitacoes_total ?? 0}
          hint={`${k.queries_boletim ?? 0} boletins ativos`}
          icon={<IconGavel width={14} height={14} />}
        />
      </div>

      {/* --- Integrações + Feed --- */}
      <div className="mt-6 grid gap-4 fade-in fade-in-2 lg:grid-cols-3">
        {/* Integrações */}
        <div className="card lg:col-span-2 overflow-hidden">
          <div
            className="flex items-center justify-between border-b px-5 py-3"
            style={{ borderColor: "var(--border)" }}
          >
            <div>
              <h3 className="text-sm font-semibold">Integrações ativas</h3>
              <p className="text-xs" style={{ color: "var(--fg-muted)" }}>
                Conexões com sistemas externos — execuções recentes
              </p>
            </div>
            <Link
              href="/diagnostico"
              className="btn btn-ghost btn-sm"
              style={{ fontSize: 12 }}
            >
              Ver histórico <IconArrowRight width={12} height={12} />
            </Link>
          </div>
          <ul className="divide-y" style={{ borderColor: "var(--border)" }}>
            {data.integrations.map((int) => {
              const toneCls =
                int.status === "ok"
                  ? "led-ok"
                  : int.status === "pending"
                  ? "led-off"
                  : int.status === "error"
                  ? "led-err"
                  : "led-warn";
              const chipTone:
                | "success"
                | "warning"
                | "default"
                | "danger" =
                int.status === "ok"
                  ? "success"
                  : int.status === "pending"
                  ? "default"
                  : int.status === "error"
                  ? "danger"
                  : "warning";
              const chipLabel =
                int.status === "ok"
                  ? "operando"
                  : int.status === "pending"
                  ? "aguardando credencial"
                  : int.status === "error"
                  ? "falha"
                  : "ocioso";
              return (
                <li
                  key={int.key}
                  className="flex items-center gap-3 px-5 py-3 transition hover:bg-[var(--panel-alt)]"
                >
                  <span
                    className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg"
                    style={{
                      background: "var(--bg-subtle)",
                      color: "var(--fg-muted)",
                    }}
                  >
                    {INTEGRATION_ICON[int.key] ?? <IconBolt />}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className={`led ${toneCls}`} />
                      <span className="text-sm font-semibold">
                        {int.label}
                      </span>
                      <StatusBadge tone={chipTone} dot={false}>
                        {chipLabel}
                      </StatusBadge>
                    </div>
                    <div
                      className="mt-0.5 text-xs"
                      style={{ color: "var(--fg-muted)" }}
                    >
                      {int.descr}
                    </div>
                  </div>
                  <div
                    className="hidden text-right text-xs md:block"
                    style={{ color: "var(--fg-muted)" }}
                  >
                    {int.last_event}
                  </div>
                </li>
              );
            })}
          </ul>
        </div>

        {/* Feed */}
        <div className="card overflow-hidden">
          <div
            className="flex items-center justify-between border-b px-5 py-3"
            style={{ borderColor: "var(--border)" }}
          >
            <div>
              <h3 className="text-sm font-semibold">Automações recentes</h3>
              <p className="text-xs" style={{ color: "var(--fg-muted)" }}>
                Eventos do sistema nas últimas execuções
              </p>
            </div>
            <button className="btn btn-ghost btn-sm" style={{ fontSize: 12 }}>
              <IconRefresh width={12} height={12} />
            </button>
          </div>
          {data.feed.length === 0 ? (
            <div
              className="px-5 py-8 text-center text-sm"
              style={{ color: "var(--fg-muted)" }}
            >
              Nenhum evento registrado ainda.
            </div>
          ) : (
            <ol className="relative ml-0 p-5">
              {data.feed.map((e, i) => (
                <li
                  key={i}
                  className="relative pb-4 pl-6 last:pb-0"
                  style={{
                    borderLeft:
                      i < data.feed.length - 1
                        ? "1px dashed var(--border-strong)"
                        : "1px dashed transparent",
                    marginLeft: 5,
                  }}
                >
                  <span
                    className="absolute -left-[7px] top-1 flex h-[14px] w-[14px] items-center justify-center rounded-full"
                    style={{
                      background: "var(--panel)",
                      color: "var(--accent)",
                      border: "2px solid var(--accent)",
                    }}
                  >
                    {KIND_ICON[e.kind] ?? <IconBolt width={10} height={10} />}
                  </span>
                  <div className="text-[13px] font-medium leading-tight">
                    {e.title}
                  </div>
                  <div
                    className="mt-0.5 text-[11px]"
                    style={{ color: "var(--fg-muted)" }}
                  >
                    {relTime(e.ts)} · {e.by}
                  </div>
                </li>
              ))}
            </ol>
          )}
        </div>
      </div>

      {/* --- Findings por área (se houver run) --- */}
      {data.last_diagnostico_run && (
        <div className="mt-6 card fade-in fade-in-3 overflow-hidden">
          <div
            className="flex flex-wrap items-end justify-between gap-2 border-b px-5 py-4"
            style={{ borderColor: "var(--border)" }}
          >
            <div>
              <div
                className="text-[11px] font-semibold uppercase tracking-widest"
                style={{ color: "var(--fg-subtle)" }}
              >
                Último diagnóstico
              </div>
              <h3 className="mt-1 text-lg font-semibold">
                Run #{data.last_diagnostico_run.id} ·{" "}
                <span
                  className="display"
                  style={{ color: "var(--accent-strong)" }}
                >
                  {data.last_diagnostico_run.total_findings}
                </span>{" "}
                findings
              </h3>
              <p className="text-xs" style={{ color: "var(--fg-muted)" }}>
                Executado {relTime(data.last_diagnostico_run.started_at)}
              </p>
            </div>
            <Link
              href={`/diagnostico/${data.last_diagnostico_run.id}`}
              className="btn btn-secondary"
            >
              Abrir relatório <IconArrowRight width={12} height={12} />
            </Link>
          </div>
          <div className="grid gap-0 md:grid-cols-5">
            {Object.entries(data.findings_por_area).map(([area, counts]) => {
              const total =
                counts.ok +
                counts.vencendo +
                counts.vencido +
                counts.ausente;
              const okPct = total ? (counts.ok / total) * 100 : 0;
              return (
                <div
                  key={area}
                  className="border-r border-b p-5 last:border-r-0 md:border-b-0"
                  style={{ borderColor: "var(--border)" }}
                >
                  <div className="flex items-center justify-between">
                    <span
                      className="text-[11px] font-semibold uppercase tracking-widest"
                      style={{ color: "var(--fg-muted)" }}
                    >
                      {AREA_LABEL[area] ?? area}
                    </span>
                    <span
                      className="display text-xs font-bold"
                      style={{
                        color:
                          okPct >= 80
                            ? "var(--success)"
                            : okPct >= 40
                            ? "var(--warning)"
                            : "var(--danger)",
                      }}
                    >
                      {okPct.toFixed(0)}%
                    </span>
                  </div>
                  <div className="mt-3 flex items-baseline gap-1.5">
                    <span className="display text-2xl font-bold">
                      {total}
                    </span>
                    <span
                      className="text-xs"
                      style={{ color: "var(--fg-muted)" }}
                    >
                      findings
                    </span>
                  </div>
                  <div
                    className="mt-2 h-2 overflow-hidden rounded-full"
                    style={{ background: "var(--bg-subtle)" }}
                  >
                    <div
                      className="flex h-full"
                      style={{ width: "100%" }}
                    >
                      {counts.ok > 0 && (
                        <div
                          style={{
                            width: `${(counts.ok / total) * 100}%`,
                            background: "var(--success)",
                          }}
                        />
                      )}
                      {counts.vencendo > 0 && (
                        <div
                          style={{
                            width: `${(counts.vencendo / total) * 100}%`,
                            background: "var(--warning)",
                          }}
                        />
                      )}
                      {counts.vencido > 0 && (
                        <div
                          style={{
                            width: `${(counts.vencido / total) * 100}%`,
                            background: "var(--danger)",
                          }}
                        />
                      )}
                      {counts.ausente > 0 && (
                        <div
                          style={{
                            width: `${(counts.ausente / total) * 100}%`,
                            background: "var(--neutral)",
                          }}
                        />
                      )}
                    </div>
                  </div>
                  <div
                    className="mt-2 flex flex-wrap gap-1.5 text-[10.5px] font-medium"
                    style={{ color: "var(--fg-muted)" }}
                  >
                    <span style={{ color: "var(--success-fg)" }}>
                      {counts.ok} ok
                    </span>
                    {counts.vencendo ? (
                      <span style={{ color: "var(--warning-fg)" }}>
                        {counts.vencendo} vencendo
                      </span>
                    ) : null}
                    {counts.vencido ? (
                      <span style={{ color: "var(--danger-fg)" }}>
                        {counts.vencido} vencidos
                      </span>
                    ) : null}
                    {counts.ausente ? (
                      <span style={{ color: "var(--neutral-fg)" }}>
                        {counts.ausente} ausentes
                      </span>
                    ) : null}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* --- Módulos --- */}
      <div className="mt-6 fade-in fade-in-4">
        <div className="section-title">Atalhos por módulo</div>
        <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-4">
          {[
            {
              href: "/rh/funcionarios",
              label: "RH · Funcionários",
              descr: `${k.funcionarios_ativos ?? 0} ativos`,
              icon: <IconUsers />,
            },
            {
              href: "/manutencao/veiculos",
              label: "Frota · Veículos",
              descr: `${k.veiculos_ativos ?? 0} em operação`,
              icon: <IconTruck />,
            },
            {
              href: "/manutencao/partes-diarias",
              label: "Partes Diárias",
              descr: `${k.partes_total ?? 0} processadas via OCR`,
              icon: <IconSparkles />,
            },
            {
              href: "/licitacoes",
              label: "Licitações",
              descr: `${k.licitacoes_total ?? 0} acompanhadas`,
              icon: <IconGavel />,
            },
            {
              href: "/licitacoes/certidoes",
              label: "Certidões",
              descr: `${k.cert_total ?? 0} cadastradas`,
              icon: <IconDoc />,
            },
            {
              href: "/rh/afastamentos",
              label: "Afastamentos INSS",
              descr: `${k.afastados ?? 0} em curso`,
              icon: <IconShield />,
            },
            {
              href: "/obras",
              label: "Obras",
              descr: "Documentação por frente",
              icon: <IconBuilding />,
            },
            {
              href: "/licitacoes/boletins",
              label: "Boletins",
              descr: `${k.queries_boletim ?? 0} queries ativas`,
              icon: <IconBell />,
            },
          ].map((m) => (
            <Link
              key={m.href}
              href={m.href}
              className="card card-hover flex items-center gap-3 p-4"
            >
              <span
                className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg"
                style={{
                  background: "var(--accent-soft)",
                  color: "var(--accent-strong)",
                }}
              >
                {m.icon}
              </span>
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm font-semibold">
                  {m.label}
                </div>
                <div
                  className="truncate text-xs"
                  style={{ color: "var(--fg-muted)" }}
                >
                  {m.descr}
                </div>
              </div>
              <IconArrowRight width={14} height={14} />
            </Link>
          ))}
        </div>
      </div>
    </div>
  );
}
