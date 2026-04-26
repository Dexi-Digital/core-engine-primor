import type { Metadata, Viewport } from "next";

import ParteDiariaMobileForm from "./form";

export const metadata: Metadata = {
  title: "Parte Diaria — Motor Central",
  description: "Apontamento de parte diaria em campo (offline-first).",
};

// Next 15: viewport/themeColor saem do `metadata` e ficam num export
// dedicado. Esses valores precisam bater com o manifest.webmanifest
// para o browser nao reclamar de inconsistencia ao instalar o PWA.
export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  maximumScale: 1,
  userScalable: false,
  themeColor: "#0f172a",
};

export default function ParteDiariaMobilePage() {
  return (
    <main className="min-h-screen bg-slate-50 px-4 py-6 text-slate-900">
      <header className="mb-6">
        <h1 className="text-xl font-bold">Parte Diaria</h1>
        <p className="mt-1 text-sm text-slate-600">
          Apontamento em campo. Funciona sem sinal -- sincroniza quando voltar.
        </p>
      </header>
      <ParteDiariaMobileForm />
    </main>
  );
}
