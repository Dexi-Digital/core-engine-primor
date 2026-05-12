"use client";

import { useMemo, useState, useTransition } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import {
  EmptyState,
  KpiGrid,
  PageHeader,
  Section,
  StatCard,
  StatusBadge,
} from "@/components/ui/primitives";
import {
  IconBuilding,
  IconCheck,
  IconCloud,
  IconRefresh,
  IconSearch,
  IconTruck,
  IconUsers,
  IconX,
} from "@/components/ui/icons";

type Finding = {
  kind: string;
  area: string;
  entity_key: string | null;
  entity_label: string | null;
  doc_tipo: string | null;
  path: string | null;
  message: string;
};

type EntityReport = {
  area: string;
  entity_key: string;
  label: string;
  ok: boolean;
  findings: Finding[];
};

export type DiagnosticoResponse = {
  root_folder: string;
  total_files: number;
  counts: Record<string, number>;
  by_area: Record<string, EntityReport[]>;
};

type AreaKey = "all" | "dp" | "frota" | "obras" | "empresa";

const AREA_TABS: { value: AreaKey; label: string }[] = [
  { value: "all", label: "Todas" },
  { value: "dp", label: "DP" },
  { value: "frota", label: "Frota" },
  { value: "obras", label: "Obras" },
  { value: "empresa", label: "Empresa" },
];

const AREA_LABELS: Record<string, { label: string; icon: React.ReactNode }> = {
  dp: { label: "DP (funcionários)", icon: <IconUsers /> },
  frota: { label: "Frota (veículos)", icon: <IconTruck /> },
  obras: { label: "Obras", icon: <IconBuilding /> },
  empresa: { label: "Empresa", icon: <IconCloud /> },
  desconhecido: { label: "Fora do padrão", icon: <IconX /> },
};

const FINDING_TONE: Record<
  string,
  "danger" | "warning" | "info" | "accent" | "success" | "default"
> = {
  faltando: "danger",
  extra: "warning",
  fora_do_padrao: "info",
  entidade_fantasma: "accent",
  entidade_sem_pasta: "default",
  ok: "success",
};

const FINDING_LABELS: Record<string, string> = {
  faltando: "Faltando",
  extra: "Extra",
  fora_do_padrao: "Fora do padrão",
  entidade_fantasma: "Entidade fantasma",
  entidade_sem_pasta: "Sem pasta",
  ok: "OK",
};

export function DiagnosticoView({
  result,
  area,
  error,
}: {
  result: DiagnosticoResponse | null;
  area: AreaKey;
  error: string | null;
}) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [pending, startTransition] = useTransition();
  const [query, setQuery] = useState("");
  const [onlyProblems, setOnlyProblems] = useState(true);

  const setArea = (next: AreaKey) => {
    const params = new URLSearchParams(searchParams?.toString() ?? "");
    if (next === "all") params.delete("area");
    else params.set("area", next);
    const qs = params.toString();
    startTransition(() => {
      router.replace(`/diagnostico/onedrive${qs ? `?${qs}` : ""}`);
      router.refresh();
    });
  };

  const handleRefresh = () => {
    startTransition(() => router.refresh());
  };

  const totalFindings =
    (result?.counts.faltando ?? 0) +
    (result?.counts.extra ?? 0) +
    (result?.counts.fora_do_padrao ?? 0) +
    (result?.counts.entidade_fantasma ?? 0) +
    (result?.counts.entidade_sem_pasta ?? 0);

  const totalEntities = useMemo(() => {
    if (!result) return 0;
    return Object.values(result.by_area).reduce(
      (acc, reports) => acc + reports.length,
      0,
    );
  }, [result]);

  return (
    <div>
      <PageHeader
        eyebrow={
          <span className="inline-flex items-center gap-1.5">
            <IconCloud width={11} height={11} /> Diagnóstico
          </span>
        }
        title="Estrutura do OneDrive"
        subtitle={
          <>
            Compara o conteúdo do SharePoint com o padrão esperado por entidade
            (funcionário, veículo, obra, empresa). Aponta documentos
            faltantes, arquivos fora do padrão e pastas fantasma.
          </>
        }
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <AreaTabs value={area} onChange={setArea} disabled={pending} />
            <button
              type="button"
              onClick={handleRefresh}
              disabled={pending}
              className="btn btn-primary inline-flex items-center gap-1.5 disabled:cursor-not-allowed disabled:opacity-60"
            >
              <IconRefresh
                width={14}
                height={14}
                style={{
                  animation: pending ? "spin 0.8s linear infinite" : undefined,
                }}
              />
              {pending ? "Rodando…" : "Rodar agora"}
            </button>
          </div>
        }
      />

      {error ? (
        <Section title="Erro">
          <div
            className="rounded-md p-3 text-sm"
            style={{
              background: "color-mix(in srgb, var(--danger) 12%, transparent)",
              color: "var(--danger-strong, var(--danger))",
            }}
          >
            {error}
          </div>
        </Section>
      ) : !result ? (
        <Section>
          <EmptyState icon={<IconCloud />} title="Sem resultado">
            A API não retornou dados. Verifique a conexão com o Microsoft
            Graph.
          </EmptyState>
        </Section>
      ) : (
        <>
          <KpiGrid>
            <StatCard
              label="Arquivos no OneDrive"
              value={result.total_files}
              hint={
                <span className="font-mono text-[11px]">
                  {result.root_folder || "—"}
                </span>
              }
              icon={<IconCloud />}
            />
            <StatCard
              label="Entidades em conformidade"
              value={result.counts.ok ?? 0}
              unit={`/ ${totalEntities}`}
              tone="success"
              hint="Pastas com todos os docs obrigatórios presentes"
              icon={<IconCheck />}
            />
            <StatCard
              label="Documentos faltando"
              value={result.counts.faltando ?? 0}
              tone="danger"
              hint="Doc obrigatório sem arquivo no OneDrive"
            />
            <StatCard
              label="Arquivos fora do padrão"
              value={
                (result.counts.extra ?? 0) +
                (result.counts.fora_do_padrao ?? 0)
              }
              tone="warning"
              hint="Tipo desconhecido ou nome não previsto"
            />
          </KpiGrid>

          <div className="mt-5">
            <Section
              title={
                <span className="inline-flex items-center gap-2">
                  Visão por entidade
                  <StatusBadge tone="default">
                    {totalFindings} achados
                  </StatusBadge>
                </span>
              }
              action={
                <div className="flex items-center gap-3">
                  <div
                    className="relative"
                    style={{
                      width: 220,
                    }}
                  >
                    <span
                      className="pointer-events-none absolute left-2 top-1/2 -translate-y-1/2"
                      style={{ color: "var(--fg-muted)" }}
                    >
                      <IconSearch width={14} height={14} />
                    </span>
                    <input
                      value={query}
                      onChange={(e) => setQuery(e.target.value)}
                      placeholder="Buscar entidade…"
                      className="w-full rounded-md border px-7 py-1.5 text-sm outline-none"
                      style={{
                        borderColor: "var(--border)",
                        background: "var(--bg)",
                        color: "var(--fg)",
                      }}
                    />
                  </div>
                  <label className="inline-flex cursor-pointer items-center gap-1.5 text-xs select-none">
                    <input
                      type="checkbox"
                      checked={onlyProblems}
                      onChange={(e) => setOnlyProblems(e.target.checked)}
                    />
                    <span style={{ color: "var(--fg-muted)" }}>
                      só com problemas
                    </span>
                  </label>
                </div>
              }
              padded={false}
            >
              <AreaList
                byArea={result.by_area}
                query={query}
                onlyProblems={onlyProblems}
              />
            </Section>
          </div>
        </>
      )}
      <style>{`@keyframes spin { from { transform: rotate(0); } to { transform: rotate(360deg); } }`}</style>
    </div>
  );
}

function AreaTabs({
  value,
  onChange,
  disabled,
}: {
  value: AreaKey;
  onChange: (a: AreaKey) => void;
  disabled?: boolean;
}) {
  return (
    <div
      className="inline-flex rounded-md p-0.5"
      style={{
        background: "var(--bg-subtle)",
        border: "1px solid var(--border)",
      }}
    >
      {AREA_TABS.map((t) => {
        const active = t.value === value;
        return (
          <button
            key={t.value}
            type="button"
            onClick={() => onChange(t.value)}
            disabled={disabled}
            className="rounded px-2.5 py-1 text-xs font-medium transition disabled:cursor-not-allowed disabled:opacity-60"
            style={{
              background: active ? "var(--bg)" : "transparent",
              color: active ? "var(--fg)" : "var(--fg-muted)",
              boxShadow: active
                ? "0 1px 2px rgba(0,0,0,0.06), 0 0 0 1px var(--border)"
                : undefined,
            }}
          >
            {t.label}
          </button>
        );
      })}
    </div>
  );
}

function AreaList({
  byArea,
  query,
  onlyProblems,
}: {
  byArea: Record<string, EntityReport[]>;
  query: string;
  onlyProblems: boolean;
}) {
  const q = query.trim().toLowerCase();
  const areas = Object.entries(byArea).filter(([, reports]) =>
    reports.length > 0,
  );

  const filtered = areas.map(([areaKey, reports]) => {
    const visible = reports.filter((r) => {
      if (onlyProblems && r.ok) return false;
      if (!q) return true;
      return (
        r.label.toLowerCase().includes(q) ||
        r.entity_key.toLowerCase().includes(q)
      );
    });
    return { areaKey, visible, total: reports.length };
  });

  const anyVisible = filtered.some((f) => f.visible.length > 0);

  if (!anyVisible) {
    return (
      <div className="p-5">
        <EmptyState icon={<IconCheck />} title="Tudo em conformidade">
          {onlyProblems
            ? "Nenhuma entidade com problemas no escopo atual. Desmarque “só com problemas” pra ver as que estão OK."
            : "Nenhuma entidade corresponde à busca."}
        </EmptyState>
      </div>
    );
  }

  return (
    <div>
      {filtered.map(({ areaKey, visible, total }) => {
        if (visible.length === 0) return null;
        const meta = AREA_LABELS[areaKey] ?? {
          label: areaKey,
          icon: <IconCloud />,
        };
        const problems = visible.filter((r) => !r.ok).length;
        const oks = visible.length - problems;
        return (
          <details
            key={areaKey}
            open
            className="border-t first:border-t-0"
            style={{ borderColor: "var(--border)" }}
          >
            <summary
              className="flex cursor-pointer items-center justify-between px-5 py-3"
              style={{ background: "var(--panel-alt)" }}
            >
              <div className="flex items-center gap-2">
                <span
                  className="flex h-7 w-7 items-center justify-center rounded-md"
                  style={{
                    background: "var(--accent-soft)",
                    color: "var(--accent-strong)",
                  }}
                >
                  {meta.icon}
                </span>
                <h3
                  className="text-sm font-semibold"
                  style={{ color: "var(--fg)" }}
                >
                  {meta.label}
                </h3>
                <span
                  className="text-xs"
                  style={{ color: "var(--fg-muted)" }}
                >
                  {visible.length} de {total}
                </span>
              </div>
              <div className="flex items-center gap-1.5">
                {problems > 0 && (
                  <StatusBadge tone="danger">{problems} com problema</StatusBadge>
                )}
                {oks > 0 && (
                  <StatusBadge tone="success">{oks} OK</StatusBadge>
                )}
              </div>
            </summary>
            <ul>
              {visible.map((r) => (
                <EntityRow key={`${areaKey}:${r.entity_key}`} report={r} />
              ))}
            </ul>
          </details>
        );
      })}
    </div>
  );
}

function EntityRow({ report }: { report: EntityReport }) {
  if (report.ok) {
    return (
      <li
        className="flex items-center justify-between border-t px-5 py-3"
        style={{ borderColor: "var(--border)" }}
      >
        <div className="flex items-center gap-2">
          <span style={{ color: "var(--success, #16a34a)" }}>
            <IconCheck width={14} height={14} />
          </span>
          <span className="text-sm" style={{ color: "var(--fg)" }}>
            {report.label}
          </span>
          <span
            className="font-mono text-[11px]"
            style={{ color: "var(--fg-subtle)" }}
          >
            {report.entity_key}
          </span>
        </div>
        <StatusBadge tone="success">OK</StatusBadge>
      </li>
    );
  }
  return (
    <li
      className="border-t px-5 py-3"
      style={{ borderColor: "var(--border)" }}
    >
      <div className="flex items-baseline justify-between gap-3">
        <div className="flex items-baseline gap-2">
          <span
            className="text-sm font-medium"
            style={{ color: "var(--fg)" }}
          >
            {report.label}
          </span>
          <span
            className="font-mono text-[11px]"
            style={{ color: "var(--fg-subtle)" }}
          >
            {report.entity_key}
          </span>
        </div>
        <span className="text-xs" style={{ color: "var(--fg-muted)" }}>
          {report.findings.length} achado
          {report.findings.length === 1 ? "" : "s"}
        </span>
      </div>
      <ul className="mt-2 flex flex-col gap-1.5">
        {report.findings.map((f, i) => (
          <li key={i} className="flex items-start gap-2 text-xs">
            <StatusBadge tone={FINDING_TONE[f.kind] ?? "default"}>
              {FINDING_LABELS[f.kind] ?? f.kind}
            </StatusBadge>
            <span
              className="leading-relaxed"
              style={{ color: "var(--fg-muted)" }}
            >
              {f.doc_tipo && (
                <code
                  className="font-mono"
                  style={{ color: "var(--fg)" }}
                >
                  {f.doc_tipo}
                </code>
              )}
              {f.doc_tipo && f.path && " — "}
              {f.path && (
                <code className="font-mono">{f.path}</code>
              )}
              {!f.doc_tipo && !f.path && f.message}
            </span>
          </li>
        ))}
      </ul>
    </li>
  );
}
