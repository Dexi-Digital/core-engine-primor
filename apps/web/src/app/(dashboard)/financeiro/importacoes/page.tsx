import Link from "next/link";
import { Importacoes } from "./view";

export default function Page() {
  return <div className="space-y-6">
    <header>
      <Link href="/financeiro" className="text-sm text-slate-500 hover:underline">← Financeiro</Link>
      <h1 className="mt-2 text-2xl font-bold">Importação financeira · Legado 90</h1>
      <p className="mt-1 max-w-3xl text-sm text-slate-600">
        Envie a base consolidada na aba “Relatório Completo”. Confira empresas, valores e
        pendências antes de ativar. Cada carga substitui a base completa do legado;
        as versões anteriores ficam no histórico. Os dados do TOTVS têm origem separada.
      </p>
    </header>
    <Importacoes />
  </div>;
}
