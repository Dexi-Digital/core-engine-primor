import Link from "next/link";

const dashboards = [
  {
    href: "/rh",
    title: "RH / DP & SESMT",
    desc: "Onboarding, dossiê de admissão, varredura documental, e-learning.",
  },
  {
    href: "/manutencao",
    title: "Manutenção & Frota",
    desc: "OCR de partes diárias, telemetria x NF, RPA despachante.",
  },
  {
    href: "/financeiro",
    title: "Financeiro & Contratos",
    desc: "TOTVS, NF-e XML, conciliação bancária, ciclo de contratos.",
  },
  {
    href: "/licitacoes",
    title: "Licitações",
    desc: "Crawlers B2G, análise municipal, habilitação de concorrentes.",
  },
  {
    href: "/juridico",
    title: "Jurídico",
    desc: "EasyJur, reconhecimento facial (prova), alertas processuais.",
  },
];

export default function Home() {
  return (
    <main className="mx-auto max-w-5xl px-6 py-16">
      <header className="mb-12">
        <p className="text-sm font-semibold uppercase tracking-widest text-slate-500">
          ZAG / PRIMOR
        </p>
        <h1 className="mt-2 text-4xl font-bold">Motor Central</h1>
        <p className="mt-3 max-w-2xl text-slate-600">
          Camada de governança que elimina ilhas de informação, integrando Domínio,
          Onvio, Tangerino, OnSafety, TOTVS, Sistema 90, OneDrive, EasyJur, Conlicitação
          e WhatsApp em um único fluxo operacional.
        </p>
      </header>

      <section className="grid grid-cols-1 gap-4 md:grid-cols-2">
        {dashboards.map((d) => (
          <Link
            key={d.href}
            href={d.href}
            className="group rounded-xl border border-slate-200 bg-white p-6 shadow-sm transition hover:border-slate-400 hover:shadow"
          >
            <h2 className="text-lg font-semibold group-hover:underline">{d.title}</h2>
            <p className="mt-2 text-sm text-slate-600">{d.desc}</p>
          </Link>
        ))}
      </section>

      <footer className="mt-16 text-xs text-slate-500">
        Scaffold inicial · v0.1.0 · consulte <code>docs/roadmap.md</code> para o
        backlog por módulo.
      </footer>
    </main>
  );
}
