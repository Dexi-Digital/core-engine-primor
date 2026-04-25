import Link from "next/link";

import { ModuleStatusCard } from "@/components/ModuleStatusCard";

export default function RHPage() {
  return (
    <div className="flex flex-col gap-6">
      <ModuleStatusCard
        title="RH / DP & SESMT"
        backendPath="/api/v1/dp-sesmt"
        scope={[
          "Cadastro de funcionários (manual + dossiê via APIs públicas).",
          "Dossiê de admissão (CEP via ViaCEP, CNPJ via BrasilAPI, CPF via DirectData).",
          "Onboarding sync com Domínio/Onvio/Tangerino/OnSafety (aguardando credenciais).",
          "Varredura documental OCR do OneDrive (ASOs e docs faltantes).",
          "Acompanhamento de INSS para afastados (alertas e documentos periódicos).",
        ]}
      />
      <nav className="rounded-lg border border-slate-200 bg-white p-4">
        <h2 className="text-sm font-semibold">Atalhos</h2>
        <ul className="mt-2 flex flex-col gap-1 text-sm">
          <li>
            <Link
              href="/rh/funcionarios"
              className="text-slate-700 hover:text-slate-900 hover:underline"
            >
              → Funcionários (cadastro + dossiê)
            </Link>
          </li>
        </ul>
      </nav>
    </div>
  );
}
